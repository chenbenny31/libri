"""Local Part B batch runner. Proposed export contract, not an OCaml importer.

Run from the NLP workspace with: libri/.venv/bin/python -m libri.b_pipeline.run
Use --observed to replay a frozen probe result; otherwise parse locally.
Plain mode sends item['text']; opt-in atomic mode also uses source code spans.
No LLM, download, training, or gold reader.
"""

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import time

from .extract import MODAL_FORCE, RULE_VERSION, extract_sentence, pp_syntax_notes, frame_grammar, mention_metadata, expected_role_phrase, expected_unresolved_conditions, expected_purpose_contract
from .local_qualifiers import KINDS as LOCAL_QUALIFIER_KINDS, local_qualifier_spec
from .semantic_bindings import KINDS as BINDING_KINDS, binding_specs
from .code_input import (validate_source_code, validate_representation, code_fingerprint,
                         plan_for_payload, document_from_plan, code_mentions,
                         boundary_issues, apply_boundary_guard)


HERE = Path(__file__).resolve().parent
LIBRI = HERE.parent
SCHEMA = "libri-b-candidate-0.11-local"


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def validate_span(text, span):
    if (not isinstance(span, list) or len(span) != 2
            or any(type(n) is not int for n in span)
            or not 0 <= span[0] < span[1] <= len(text)):
        raise ValueError(f"Invalid nonempty Unicode character span: {span!r}")


def validate_sentence(text, sentence):
    words, tokens = sentence["words"], sentence["tokens"]
    if not words or not tokens:
        raise ValueError("Empty sentence annotations")
    ids = [w["id"] for w in words]
    if ids != list(range(1, len(words) + 1)):
        raise ValueError("Word IDs must be ordered, unique, and sentence-local")
    by_id = {w["id"]: w for w in words}
    if sum(w["head"] == 0 for w in words) != 1:
        raise ValueError("Sentence must have exactly one root")
    for w in words:
        if w["head"] not in {0, *by_id}:
            raise ValueError("Dangling dependency head")
        seen, current = set(), w["id"]
        while current:
            if current in seen:
                raise ValueError("Dependency cycle")
            seen.add(current)
            current = by_id[current]["head"]
        start, end = w.get("start_char"), w.get("end_char")
        if start is not None and end is not None:
            validate_span(text, [start, end])
            if text[start:end] != w["text"]:
                raise ValueError("Word-to-text alignment mismatch")
    token_word_ids = []
    previous_end = -1
    for token in tokens:
        start, end = token["start_char"], token["end_char"]
        validate_span(text, [start, end])
        if text[start:end] != token["text"]:
            raise ValueError("Token-to-text alignment mismatch")
        if start < previous_end:
            raise ValueError("Tokens must be ordered and nonoverlapping")
        previous_end = end
        for wid in token["word_ids"]:
            if wid not in by_id:
                raise ValueError("Token refers to a missing word")
            word = by_id[wid]
            ws, we = word.get("start_char"), word.get("end_char")
            if (ws is None) != (we is None):
                raise ValueError("Word offsets must both exist or both be null")
            if ws is not None and not start <= ws < we <= end:
                raise ValueError("Word span is outside its owning token")
        token_word_ids.extend(token["word_ids"])
    if token_word_ids != ids:
        raise ValueError("Token-word mapping must cover each word once")
    span = [tokens[0]["start_char"], tokens[-1]["end_char"]]
    if text[span[0]:span[1]] != sentence["text"]:
        raise ValueError("Sentence-to-text alignment mismatch")
    return span


def validate_evidence(text, obj):
    """Check every evidence object recursively, including discontinuous cues."""
    if isinstance(obj, dict):
        if "spans" in obj and "text" in obj:
            spans = obj["spans"]
            if not spans:
                raise ValueError("Evidence cannot have empty spans")
            for span in spans:
                validate_span(text, span)
            if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
                raise ValueError("Evidence spans overlap or are out of order")
            if " ".join(text[a:b] for a, b in spans) != obj["text"]:
                raise ValueError("Evidence text does not match source slices")
        for value in obj.values():
            validate_evidence(text, value)
    elif isinstance(obj, list):
        for value in obj:
            validate_evidence(text, value)


def validate_pp_candidates(text, candidate, sentence=None):
    """Reject false bindings and evidence inconsistent with the supplied tree.

    This validates serialization and parser fidelity, not linguistic truth.
    """
    if not isinstance(candidate.get("pp_candidates"), list):
        raise ValueError("Current schema requires a PP candidate list, including an empty one")
    pps = candidate["pp_candidates"]
    ids = [p["id"] for p in pps]
    other_ids = {f["id"] for f in candidate["frames"]} | {q["id"] for q in candidate["qualifiers"]}
    if ids != [f"p{i}" for i in range(1, len(pps) + 1)] or other_ids.intersection(ids):
        raise ValueError("Invalid PP candidate IDs")
    by_id = {w["id"]: w for w in sentence["words"]} if sentence else {}
    def descendants(wid):
        return {wid}.union(*(descendants(w["id"]) for w in by_id.values() if w["head"] == wid))
    def check_evidence(evidence, expected_ids):
        if evidence.get("word_ids") != sorted(expected_ids):
            raise ValueError("PP evidence word IDs do not match parser structure")
        bounds = []
        for wid in expected_ids:
            word = by_id[wid]
            start, end = word.get("start_char"), word.get("end_char")
            if start is None or end is None:
                token = next(t for t in sentence["tokens"] if wid in t["word_ids"])
                start, end = token["start_char"], token["end_char"]
            bounds.append((start, end))
        spans = []
        for start, end in sorted(set(bounds)):
            if spans and (start <= spans[-1][1] or text[spans[-1][1]:start].isspace()):
                spans[-1][1] = max(end, spans[-1][1])
            else:
                spans.append([start, end])
        if evidence.get("spans") != spans:
            raise ValueError("PP evidence spans do not match parser word IDs")
    required = {"id", "preposition", "object", "phrase", "nominal_head", "parser_attachment",
                "frame_context", "syntax_pattern", "syntax_notes", "boundary_status",
                "semantic_role", "semantic_binding", "is_independent_assertion"}
    heads = []
    for pp in pps:
        if set(pp) != required or set(pp["parser_attachment"]) != {"deprel", "governor"}:
            raise ValueError("Unknown or missing PP fields; semantic target is not supported")
        if (pp["semantic_role"] != "not_determined" or pp["semantic_binding"] != "unresolved"
                or pp["is_independent_assertion"] is not False
                or pp["boundary_status"] != "parser_subtree_not_validated"):
            raise ValueError("A PP observation cannot claim resolved semantics or boundaries")
        if (not isinstance(pp["syntax_notes"], list)
                or any(not isinstance(n, str) or not n for n in pp["syntax_notes"])
                or pp["syntax_pattern"] != ("complex_nominal" if pp["syntax_notes"] else "simple_nominal")):
            raise ValueError("Inconsistent PP syntax label/notes")
        attachment = pp["parser_attachment"]
        if attachment["deprel"] not in {"obl", "nmod"}:
            raise ValueError("Unsupported PP dependency relation")
        if pp["frame_context"] is not None and pp["frame_context"] not in {f["id"] for f in candidate["frames"]}:
            raise ValueError("Dangling PP frame context")
        if sentence is None:
            continue
        head_ids = pp["nominal_head"]["word_ids"]
        if len(head_ids) != 1 or head_ids[0] not in by_id:
            raise ValueError("PP must identify one existing nominal head")
        head = by_id[head_ids[0]]
        heads.append(head["id"])
        cases = [w for w in by_id.values() if w["head"] == head["id"] and w["deprel"] == "case"]
        if (head["upos"] not in {"NOUN", "PROPN"} or head["deprel"] != attachment["deprel"]
                or not any(w["upos"] == "ADP" and (w.get("lemma") or w["text"]).lower() in {"with", "to"} for w in cases)):
            raise ValueError("PP observation is outside the supported parser pattern")
        case_ids = set().union(*(descendants(w["id"]) for w in cases))
        phrase_ids = descendants(head["id"])
        if pp["syntax_notes"] != pp_syntax_notes(text, sentence["words"], head["id"], case_ids, phrase_ids):
            raise ValueError("PP syntax notes do not match the saved parser tree")
        for field, expected in (("nominal_head", {head["id"]}), ("preposition", case_ids),
                                ("phrase", phrase_ids), ("object", phrase_ids - case_ids)):
            check_evidence(pp[field], expected)
        check_evidence(attachment["governor"], {head["head"]})
        contexts = [f["id"] for f in candidate["frames"] if head["head"] in f["predicate"]["word_ids"]]
        if pp["frame_context"] != (contexts[0] if len(contexts) == 1 else None):
            raise ValueError("PP frame context differs from parser governor")
    if heads != sorted(set(heads)):
        raise ValueError("PP heads must be unique and ordered")
    if sentence is not None:
        expected_heads = [w["id"] for w in by_id.values()
                          if w["upos"] in {"NOUN", "PROPN"} and w["deprel"] in {"obl", "nmod"}
                          and any(c["head"] == w["id"] and c["deprel"] == "case" and c["upos"] == "ADP"
                                  and (c.get("lemma") or c["text"]).lower() in {"with", "to"}
                                  for c in by_id.values())]
        if heads != expected_heads:
            raise ValueError("PP inventory omits a supported parser observation")


def validate_candidate(text, candidate, sentence=None, source_item=None):
    if source_item is not None and source_item["text"] != text:
        raise ValueError("Semantic binding source text differs")
    verified_code = code_mentions(source_item, sentence) if source_item is not None and sentence is not None else []
    if candidate["status"] not in {"pattern_covered", "partial", "unsupported", "ambiguous"}:
        raise ValueError("Unknown candidate status")
    if candidate["status"] != "pattern_covered" and candidate["candidate_structure"] is not None:
        raise ValueError("Incomplete candidates must not export a complete structure")
    frames, qualifiers = candidate["frames"], candidate["qualifiers"]
    frame_ids, qualifier_ids = {f["id"] for f in frames}, {q["id"] for q in qualifiers}
    if len(frame_ids) != len(frames) or len(qualifier_ids) != len(qualifiers):
        raise ValueError("Duplicate local frame/qualifier IDs")
    if frame_ids & qualifier_ids:
        raise ValueError("Frame and qualifier IDs must not collide")
    if any(q["target"] not in frame_ids for q in qualifiers):
        raise ValueError("Dangling qualifier target")
    if candidate["status"] == "pattern_covered":
        if not frames or candidate["candidate_structure"] is None:
            raise ValueError("Covered candidates require frames and a complete root structure")
        if candidate["reasons"] or any(q["interpretation_status"] != "supported" for q in qualifiers):
            raise ValueError("Covered candidate contains unresolved issues")
    if candidate["assertion_policy"] != "source_required":
        raise ValueError("Original source must remain attached")
    for frame in frames:
        if not frame["predicate"].get("spans") or not frame["predicate"].get("text") or not frame["predicate"].get("lemma"):
            raise ValueError("Frame predicate requires aligned evidence and a lemma")
        if (frame.get("voice") not in {"active", "passive", "not_applicable"}
                or frame.get("clause_form") not in {"imperative", "unspecified"}
                or frame.get("subject_status") not in {"explicit", "implicit_addressee", "unresolved"}
                or type(frame.get("head_word_id")) is not int
                or frame["head_word_id"] not in frame["predicate"].get("word_ids", [])):
            raise ValueError("Frame requires grammatical metadata and a predicate head")
        subject_roles = [r for r in frame["roles"] if r["role"] == "subject"]
        implicit = frame["subject_status"] == "implicit_addressee"
        if (frame["subject_status"] == "explicit") != (len(subject_roles) == 1):
            raise ValueError("Explicit subject metadata and role inventory disagree")
        if implicit and (frame["clause_form"] != "imperative" or frame["voice"] != "active" or subject_roles):
            raise ValueError("Implicit addressee cannot have fabricated explicit subject/voice")
        if frame["clause_form"] == "imperative" and not implicit:
            raise ValueError("Imperative metadata is inconsistent")
        if candidate["status"] == "pattern_covered" and not (len(subject_roles) == 1 or implicit):
            raise ValueError("Covered frames need explicit subject or tree-backed imperative")
        if sentence is not None:
            grammar = frame_grammar(sentence["words"], frame["head_word_id"])
            if grammar is None or any(frame[k] != v for k, v in grammar.items()):
                raise ValueError("Frame grammar does not match saved parse")
            direct = [w for w in sentence["words"] if w["head"] == frame["head_word_id"]]
            subject_rel = "nsubj:pass" if frame["voice"] == "passive" else "nsubj"
            expected_roles = []
            for role_name, relation in [("subject", subject_rel), ("object", "obj")]:
                heads = [w["id"] for w in direct if w["deprel"] == relation
                         and w["id"] not in frame["predicate"]["word_ids"]]
                if len(heads) == 1:
                    expected_roles.append((role_name, heads[0]))
            if sorted((r["role"], r.get("head_word_id")) for r in frame["roles"]) != sorted(expected_roles):
                raise ValueError("Direct role inventory differs from saved parse")
        elif implicit:
            raise ValueError("Imperative subject exemption requires the saved parse")
        for role in frame["roles"]:
            if (role.get("role") not in {"subject", "object"} or type(role.get("head_word_id")) is not int
                    or role["head_word_id"] not in role["phrase"].get("word_ids", [])
                    or role.get("mention_type") not in {"nominal", "second_person", "unresolved_pronoun", "other"}
                    or role.get("reference_status") not in {"source_mention_only", "document_addressee", "unresolved"}):
                raise ValueError("Role requires source mention metadata")
            expected_rel = ("nsubj:pass" if frame["voice"] == "passive" else "nsubj") if role["role"] == "subject" else "obj"
            if role.get("syntactic_relation") != expected_rel:
                raise ValueError("Role grammatical relation does not match voice")
            if candidate["status"] == "pattern_covered" and (role["reference_status"] == "unresolved"
                    or role["mention_type"] in {"unresolved_pronoun", "other"}):
                raise ValueError("Covered candidate cannot contain an unresolved reference/mention")
            if sentence is not None:
                if any(role[k] != v for k, v in mention_metadata(sentence["words"], role["head_word_id"]).items()):
                    raise ValueError("Mention metadata does not match saved parse")
                head_word = next(w for w in sentence["words"] if w["id"] == role["head_word_id"])
                if head_word["head"] != frame["head_word_id"]:
                    raise ValueError("Role head is not attached to the frame predicate")
                if role['phrase'] != expected_role_phrase(text,sentence,frame['head_word_id'],role['head_word_id'],role['role'],verified_code):
                    raise ValueError('Role phrase word IDs and source spans differ from complete expected evidence')
        if frame["voice"] == "passive":
            passive_aux = [q for q in qualifiers if q["target"] == frame["id"] and q["kind"] == "passive_auxiliary"]
            if len(passive_aux) != 1 or passive_aux[0]["interpretation_status"] != "supported":
                raise ValueError("Passive voice needs an evidenced auxiliary")
            if sentence is not None:
                expected_aux = [w["id"] for w in sentence["words"] if w["head"] == frame["head_word_id"] and w["deprel"] == "aux:pass"]
                if passive_aux[0]["cue"]["word_ids"] != expected_aux:
                    raise ValueError("Passive auxiliary evidence does not match parse")
        if frame["is_independent_assertion"] is not False:
            raise ValueError("A frame cannot be promoted to an independent fact")
        if any(q not in qualifier_ids for q in frame.get("qualifier_ids", [])):
            raise ValueError("Dangling frame qualifier reference")
        linked = frame.get("qualifier_ids", [])
        targeted = {q["id"] for q in qualifiers if q["target"] == frame["id"]}
        if len(linked) != len(set(linked)) or set(linked) != targeted:
            raise ValueError("Frame qualifier links must match targets in both directions")
    for q in qualifiers:
        if q["target"] not in frame_ids:
            raise ValueError("Dangling qualifier target")
        if q.get("condition_frame") is not None and q["condition_frame"] not in frame_ids:
            raise ValueError("Dangling condition frame")
        if q["kind"] == "modal" and q["interpretation_status"] == "supported":
            if q.get("modal_force") not in set(MODAL_FORCE.values()):
                raise ValueError("Supported modal qualifier needs a known lexical force")
            if q.get("modal_flavor") != "not_determined":
                raise ValueError("B does not claim modal flavor (rule vs. inference)")
        if q['kind'] in LOCAL_QUALIFIER_KINDS:
            if sentence is None:
                raise ValueError('Local semantic qualifiers require the saved parser tree')
            if type(q.get('attachment_head_word_id')) is not int:
                raise ValueError('Local qualifier requires an integer attachment head')
            target = next(f for f in frames if f['id']==q['target'])
            spec = local_qualifier_spec(text, sentence['words'], target['head_word_id'], q.get('attachment_head_word_id'))
            if spec is None:
                raise ValueError('Local qualifier does not satisfy its bounded rule')
            spec = dict(spec)
            cue_ids, argument_ids = spec.pop('cue_ids'), spec.pop('argument_ids')
            expected_keys = {'id','cue','argument','target','interpretation_status'} | set(spec)
            if (set(q)!=expected_keys or json.dumps({k:q.get(k) for k in spec},sort_keys=True)
                    != json.dumps(spec,sort_keys=True)):
                raise ValueError('Local qualifier metadata or semantic target mismatch')
            if q['interpretation_status']!='supported' or q['cue'].get('word_ids')!=cue_ids or q['argument'].get('word_ids')!=argument_ids:
                raise ValueError('Local qualifier evidence incomplete or misbound')
            role = q['role_target']
            if role is not None and not any(r['role']==role['role'] and r['head_word_id']==role['head_word_id'] for r in target['roles']):
                raise ValueError('Local configuration requires the actual object role')
    local_keys = [(q['target'],q['attachment_head_word_id']) for q in qualifiers if q['kind'] in LOCAL_QUALIFIER_KINDS]
    if len(set(local_keys))!=len(local_keys):
        raise ValueError('Duplicate local semantic qualifier')
    if sentence is not None:
        expected_local = {(f['id'],w['id']) for f in frames for w in sentence['words']
                          if local_qualifier_spec(text,sentence['words'],f['head_word_id'],w['id']) is not None}
        if set(local_keys)!=expected_local:
            raise ValueError('Local semantic qualifier inventory is incomplete')
    expected_bindings = {}
    if sentence is not None:
        expected_bindings = {(f['id'], s['attachment_head_word_id']): s for f in frames
                             for s in binding_specs(text, sentence['words'], f['head_word_id'], verified_code)}
    binding_keys = []
    for q in qualifiers:
        if q['kind'] not in BINDING_KINDS:
            continue
        if type(q.get('attachment_head_word_id')) is not int:
            raise ValueError('Semantic binding requires an integer attachment head')
        key = (q['target'], q['attachment_head_word_id'])
        binding_keys.append(key)
        spec = expected_bindings.get(key)
        if spec is None:
            raise ValueError('Semantic binding lacks a bounded rule or verified source')
        spec = dict(spec)
        cue_ids, argument_ids = spec.pop('cue_ids'), spec.pop('argument_ids')
        if (set(q) != {'id', 'cue', 'argument', 'target', 'interpretation_status'} | set(spec)
                or json.dumps({k:q[k] for k in spec}, sort_keys=True) != json.dumps(spec, sort_keys=True)
                or q['interpretation_status'] != 'supported'
                or q['cue'].get('word_ids') != cue_ids or q['argument'].get('word_ids') != argument_ids):
            raise ValueError('Semantic binding metadata, evidence, or target mismatch')
    if len(set(binding_keys)) != len(binding_keys) or set(binding_keys) != set(expected_bindings):
        raise ValueError('Semantic binding inventory incomplete or duplicated')
    fallbacks = [q for q in qualifiers if q['kind'] == 'unresolved_condition']
    if sentence is None:
        if fallbacks:
            raise ValueError('Unresolved condition evidence requires the saved parser tree')
    else:
        # Extraction never calls this validator: replay is safe here and checks
        # even deletion/renaming of a fallback together with its frame link.
        # Exact canonical comparison also rejects bool/int substitutions and
        # evidence that is individually aligned but omits no/even/if or a role.
        expected_fallbacks = expected_unresolved_conditions(text, sentence, verified_code)
        if json.dumps(fallbacks, sort_keys=True) != json.dumps(expected_fallbacks, sort_keys=True):
            raise ValueError('Unresolved condition inventory, evidence, target or status differs from saved parse')
        # A correct reference ID is insufficient if the actual referenced frame
        # was replaced with a different, otherwise well-formed source event.
        root_head = next(w['id'] for w in sentence['words'] if w['head'] == 0)
        by_frame = {f['id']: f for f in frames}
        for q in fallbacks:
            if by_frame[q['target']]['head_word_id'] != root_head:
                raise ValueError('Unresolved condition target must be the actual main predicate')
            if (q['condition_frame'] is not None and
                    by_frame[q['condition_frame']]['head_word_id'] != q['condition_head_word_id']):
                raise ValueError('Unresolved condition frame head differs from condition evidence')
    purposes = [q for q in qualifiers if q['kind'] == 'purpose']
    if sentence is None:
        if purposes:
            raise ValueError('Purpose evidence requires the saved parser tree')
    else:
        expected_purposes, expected_frame_heads, expected_parent_context = expected_purpose_contract(text, sentence, verified_code)
        if json.dumps(purposes, sort_keys=True) != json.dumps(expected_purposes, sort_keys=True):
            raise ValueError('Purpose inventory, nested evidence, targets or interpretation differs from saved parse')
        if expected_purposes:
            # Purpose remains under the parent's actual context. Removing or
            # moving not/can/if while preserving the purpose itself is also an
            # invalid export, even when the outer candidate remains partial.
            if json.dumps(qualifiers, sort_keys=True) != json.dumps(expected_parent_context, sort_keys=True):
                raise ValueError('Purpose requires complete original parent and condition qualifier context')
            # A reference ID is insufficient if a consumer replaces its frame,
            # or promotes a nested purpose action into a top-level event.
            if [(f['id'], f['head_word_id']) for f in frames] != expected_frame_heads:
                raise ValueError('Purpose requires original top-level frame inventory and heads')
            if candidate['candidate_structure'] is not None or candidate['status'] == 'pattern_covered':
                raise ValueError('Purpose composition is unresolved, not a complete assertion')
            if 'purpose_composition_not_supported' not in candidate['reasons']:
                raise ValueError('Purpose composition limitation must remain explicit')
            by_frame = {f['id']: f for f in frames}
            for q in purposes:
                actual = by_frame[q['target']]
                if actual['head_word_id'] != q['parent_head_word_id'] or not any(
                        r['role'] == q['tool_role']['role'] and
                        r['head_word_id'] == q['tool_role']['head_word_id'] for r in actual['roles']):
                    raise ValueError('Purpose target and tool must reference the actual parent frame')
    def structure_refs(node):
        if not isinstance(node, dict):
            raise ValueError("Structure operator child/root must be a non-null node")
        if node["kind"] == "frame":
            if node["frame"] not in frame_ids:
                raise ValueError("Dangling structure frame")
        elif node["kind"] in {"negation", "default"}:
            structure_refs(node["child"])
        elif node["kind"] == "modal":
            if node.get("force") not in set(MODAL_FORCE.values()):
                raise ValueError("Unknown modal force")
            structure_refs(node["child"])
        elif node["kind"] == "conditional":
            if node["condition"] not in frame_ids:
                raise ValueError("Dangling structure condition")
            structure_refs(node["consequent"])
            if node.get("condition_structure") is not None:
                structure_refs(node["condition_structure"])
        else:
            raise ValueError("Unknown structure operator")
    if candidate["candidate_structure"] is not None:
        structure_refs(candidate["candidate_structure"])
    for q in qualifiers:
        if q.get("condition_structure") is not None:
            structure_refs(q["condition_structure"])
        if q.get("scope_candidate") is not None:
            if q.get("scope_candidate_is_complete_claim") is not False:
                raise ValueError("A local scope candidate cannot claim completeness")
            structure_refs(q["scope_candidate"])
            if any(qid not in qualifier_ids for qid in q.get("scope_candidate_excluded_qualifier_ids", [])):
                raise ValueError("Dangling excluded qualifier reference")
    validate_pp_candidates(text, candidate, sentence)
    validate_evidence(text, candidate)
    if sentence is not None:
        word_ids = {w["id"] for w in sentence["words"]}
        by_id = {w["id"]: w for w in sentence["words"]}
        def aligned_spans(ids):
            spans = []
            for wid in ids:
                word = by_id[wid]
                if word.get("start_char") is not None and word.get("end_char") is not None:
                    spans.append((word["start_char"], word["end_char"]))
                else:
                    token = next((t for t in sentence.get("tokens", []) if wid in t["word_ids"]), None)
                    if token is None:
                        raise ValueError("Evidence word has no aligned source token")
                    spans.append((token["start_char"], token["end_char"]))
            merged = []
            for start, end in sorted(set(spans)):
                if merged and (start <= merged[-1][1] or text[merged[-1][1]:start].isspace()):
                    merged[-1][1] = max(end, merged[-1][1])
                else:
                    merged.append([start, end])
            return merged
        def check_word_refs(obj):
            if isinstance(obj, dict):
                if "word_ids" in obj:
                    ids = obj["word_ids"]
                    if not ids or any(type(i) is not int for i in ids) or ids != sorted(set(ids)) or not set(ids) <= word_ids:
                        raise ValueError("Evidence contains invalid sentence-local word references")
                    if obj.get("spans") != aligned_spans(ids):
                        raise ValueError("Evidence word IDs and source spans disagree")
                for value in obj.values():
                    check_word_refs(value)
            elif isinstance(obj, list):
                for value in obj:
                    check_word_refs(value)
        check_word_refs(candidate)


def head_evidence(text, word):
    span = [word.get("start_char"), word.get("end_char")]
    validate_span(text, span)
    return {"text": text[span[0]:span[1]], "lemma": word["lemma"],
            "spans": [span], "word_ids": [word["id"]]}


def literal_svo(text, sentence):
    """Deliberately simple, separately labelled head-only baseline."""
    words = sentence["words"]
    roots = [w for w in words if w["head"] == 0 and w["upos"] == "VERB"]
    result = {"variant": "literal_svo_heads", "triples": [],
              "note": "Drops modifiers and scope by design; evaluation only, not a safe user-facing claim."}
    if len(roots) != 1:
        result["reason"] = "no_single_verbal_root"
        return result
    root = roots[0]
    subjects = [w for w in words if w["head"] == root["id"] and w["deprel"] == "nsubj"]
    objects = [w for w in words if w["head"] == root["id"] and w["deprel"] == "obj"]
    if len(subjects) != 1 or len(objects) != 1:
        result["reason"] = "requires_one_direct_subject_and_object"
        return result
    result["triples"] = [{"subject": head_evidence(text, subjects[0]),
                          "verb": head_evidence(text, root),
                          "object": head_evidence(text, objects[0])}]
    return result


def frames_only(candidate):
    frames = deepcopy(candidate["frames"])
    for frame in frames:
        frame.pop("qualifier_ids", None)
    return {"variant": "frames_without_scope", "frames": frames,
            'ablation_definition':'all_qualifiers_removed_including_manner_time_result_configuration_method_destination_purpose',
            "qualifiers": [], "candidate_structure": None,
            "parent_status": candidate["status"],
            "scope_ablation_eligible": candidate["status"] == "pattern_covered",
            "note": "Same frame inventory and eligibility as full variant; all qualifiers removed, including time/result/configuration/method/destination and nested purpose as well as scope. Legacy key kept for compatibility. Score this payload, not raw evidence. Eligibility is NOT a dataset filter; all inputs remain in denominators. Partial fragments are diagnostic only."}


def scope_only_ablation(candidate):
    """Separate operator removal from loss of local content and PP bindings."""
    removed_kinds = {'negation','modal','default','if','only_after','unresolved_condition','purpose'}
    retained = [deepcopy(q) for q in candidate['qualifiers'] if q['kind'] not in removed_kinds]
    retained_ids = {q['id'] for q in retained}
    frames = deepcopy(candidate['frames'])
    for f in frames:
        f['qualifier_ids'] = [qid for qid in f['qualifier_ids'] if qid in retained_ids]
    return {'variant':'scope_operators_removed', 'removed_kinds':sorted(removed_kinds),
            'frames':frames,'qualifiers':retained,'candidate_structure':None,
            'parent_status':candidate['status'],
            'scope_ablation_eligible':candidate['status']=='pattern_covered',
            'note':'Evaluation-only ablation: removes selected negation/modal/condition/default operators, including unresolved_condition and nested purpose; retains manner/time/result/configuration/method/destination content. Unmodeled cue text, purpose text and condition frames can remain: this is node removal, not erasure of all scope information. Same input/frame denominator. No independent assertions or safe user-facing claims.'}


def provenance_check(item):
    """Separate local source checks from parser/extractor runtime success."""
    source = item.get("source", {})
    if not item.get("kind", "").startswith("real"):
        return {"status": "synthetic_or_unspecified", "source_is_real_document": False}
    required = ("url", "revision", "file", "file_sha256")
    missing = [key for key in required if not source.get(key)]
    if missing:
        return {"status": "incomplete_metadata", "missing": missing}
    local = (LIBRI / source["file"]).resolve()
    if not local.is_relative_to(LIBRI.resolve()) or not local.is_file():
        return {"status": "metadata_only_local_snapshot_unavailable"}
    if digest_file(local) != source["file_sha256"]:
        raise ValueError("Source snapshot hash mismatch")
    if source.get("raw_paragraph_span"):
        raw = local.read_text(encoding="utf-8")
        span = source["raw_paragraph_span"]
        validate_span(raw, span)
        if source.get("raw_paragraph_sha256") and digest_bytes(raw[slice(*span)].encode("utf-8")) != source["raw_paragraph_sha256"]:
            raise ValueError("Source paragraph hash mismatch")
    return {"status": "local_snapshot_hash_verified", "mapping": "paragraph level only; not a per-word original-file map"}


def process_record(item, parsed):
    record = {"id": item["id"], "kind": item.get("kind", "unspecified"),
              "input": deepcopy(item), "runtime_status": "ok", "candidate": None,
              "variants": {}, "syntax": parsed.get("sentences", []) if parsed else []}
    if parsed and parsed.get("error"):
        record.update(runtime_status="parse_error", error=parsed["error"])
        record['parser_failure'] = {'stage':parsed.get('error_stage', 'not_recorded'),
                                    'requested_code_mode':parsed.get('requested_code_mode', 'not_recorded'),
                                    'normal_parse_preserved':bool(parsed.get('plain_parse'))}
        return record
    try:
        text = item["text"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Input text must be a nonempty string")
        declared_text_hash = item.get("preparation", {}).get("text_sha256")
        if declared_text_hash and declared_text_hash != digest_bytes(text.encode("utf-8")):
            raise ValueError("Prepared-text provenance hash mismatch")
        record["provenance"] = provenance_check(item)
        if not parsed or parsed["text"] != text or parsed["text_sha256"] != digest_bytes(text.encode("utf-8")):
            raise ValueError("Saved parse missing or does not match the input")
        sentences = parsed["sentences"]
        spans = [validate_sentence(text, sentence) for sentence in sentences]
        mode = validate_representation(item, parsed, validate_sentence)
        record['representation'] = deepcopy(parsed.get('representation', {'mode': mode}))
        target = item.get("target_span")
        if target is None:
            if len(spans) != 1:
                raise ValueError("Multi-sentence context needs an explicit target_span")
            target = spans[0]
        validate_span(text, target)
        matches = [s for s, span in zip(sentences, spans) if span == target]
        if len(matches) != 1:
            raise ValueError("Target must align to exactly one complete parsed sentence")
        record["target_span"] = target
        record["target_sentence_id"] = matches[0]["sentence_id"]
        record['code_mentions'] = code_mentions(item, matches[0])
        candidate = extract_sentence(text, matches[0], record['code_mentions'])
        record['code_boundary_issues'] = boundary_issues(candidate, record['code_mentions'])
        apply_boundary_guard(candidate, record['code_boundary_issues'])
        validate_candidate(text, candidate, matches[0], source_item=item)
        record["candidate"] = candidate
        try:
            svo = literal_svo(text, matches[0])
        except Exception as exc:
            svo = {"variant": "literal_svo_heads", "triples": [], "error": str(exc)}
        record["variants"] = {"literal_svo": svo, "frames_without_scope": frames_only(candidate),
                              'scope_operators_removed':scope_only_ablation(candidate)}
        record["scope_ablation_eligible"] = candidate["status"] == "pattern_covered"
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        record.update(runtime_status="alignment_or_contract_error", candidate=None,
                      variants={}, error={"type": type(exc).__name__, "message": str(exc)})
    except Exception as exc:
        # A failed extraction must not remove other inputs from the denominator.
        # KeyboardInterrupt/SystemExit deliberately remain outside Exception.
        record.update(runtime_status="extraction_error", candidate=None,
                      variants={}, error={"type": type(exc).__name__, "message": str(exc)})
    return record


def parse_locally(inputs, input_path, models, code_mode='plain'):
    # Import lazily: replaying saved annotations needs only Python's standard library.
    from importlib.metadata import version
    from libri.experiments.stanza_probe.probe import annotations, PROCESSORS, DEFAULT_RESOURCES_VERSION
    import stanza
    import torch
    if code_mode not in {'plain', 'atomic_code'}:
        raise ValueError('Unknown requested code mode')
    torch.set_num_threads(2)
    nlp = stanza.Pipeline(lang="en", dir=str(models), package=None,
                          processors=PROCESSORS, use_gpu=False, download_method=None)
    atomic_nlp = None
    records = []
    for item in inputs:
        plain_record = None
        stage = 'input_validation'
        try:
            text = item["text"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Input text must be a nonempty string")
            payload = validate_source_code(item, required=code_mode == 'atomic_code')
            provenance_check(item)
            stage = 'normal_parse'
            doc = nlp(text)
            plain = annotations(doc, text)
            plain_record = {'id':item['id'], 'text':text,
                            'text_sha256':digest_bytes(text.encode()), 'sentences':plain,
                            'representation':{'mode':'plain'}}
            representation = {'mode': 'plain'}
            if code_mode == 'atomic_code':
                stage = 'atomic_token_plan'
                plan = plan_for_payload(plain, payload)
                if payload['spans']:
                    if atomic_nlp is None:
                        atomic_nlp = stanza.Pipeline(lang='en', dir=str(models), package=None,
                            processors=PROCESSORS, use_gpu=False, download_method=None,
                            tokenize_pretokenized=True)
                    stage = 'atomic_parse'
                    doc = atomic_nlp(document_from_plan(text, plan))
                representation = {'mode': code_mode, 'source_code_sha256': code_fingerprint(payload),
                                  'plain_sentences': plain, 'token_plan': plan}
            records.append({"id": item["id"], "kind": item.get("kind", "unspecified"),
                            "text": text, "text_sha256": digest_bytes(text.encode("utf-8")),
                            "representation": representation, "sentences": annotations(doc, text)})
        except Exception as exc:
            failure = {'id':item['id'], 'requested_code_mode':code_mode,
                       'error_stage':stage, 'error':{'type':type(exc).__name__, 'message':str(exc)}}
            if plain_record is not None:
                failure['plain_parse'] = plain_record
            records.append(failure)
    return {"artifact_type": "observed_parser_annotations_not_semantic_extraction",
            "run_at_utc": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(),
            "resources_version": DEFAULT_RESOURCES_VERSION,
            "parser_adapter_files_sha256": {"run.py": digest_file(__file__),
                                             "code_input.py": digest_file(HERE / 'code_input.py'),
                                             "probe.py": digest_file(LIBRI / "experiments" / "stanza_probe" / "probe.py")},
            "packages": {name: version(name) for name in ("stanza", "torch", "numpy")},
            "configuration": {"processors": PROCESSORS, "device": "cpu", "threads": 2,
                              "download": False, 'code_mode': code_mode},
            "resources_sha256": digest_file(models / "resources.json"),
            "models": {str(p.relative_to(models)): digest_file(p) for p in sorted(models.glob("en/**/*.pt"))},
            "inputs_sha256": digest_file(input_path), "records": records}


def build_bundle(inputs, observed):
    if observed.get("artifact_type") != "observed_parser_annotations_not_semantic_extraction":
        raise ValueError("Expected raw observed parser annotations")
    missing = [key for key in ("configuration", "models", "resources_sha256", "packages", "inputs_sha256") if not observed.get(key)]
    if missing:
        raise ValueError(f"Parser provenance incomplete: {missing}")
    hashes = [observed["inputs_sha256"], observed["resources_sha256"], *observed["models"].values()]
    if any(not isinstance(h, str) or len(h) != 64 or any(c not in "0123456789abcdef" for c in h) for h in hashes):
        raise ValueError("Malformed parser/model provenance hash")
    ids = [item["id"] for item in inputs]
    observed_ids = [item["id"] for item in observed["records"]]
    if len(set(ids)) != len(ids) or len(set(observed_ids)) != len(observed_ids):
        raise ValueError("Duplicate IDs would make comparison ambiguous")
    if set(observed_ids) != set(ids):
        raise ValueError("Parse record IDs must exactly match input IDs")
    by_id = {r["id"]: r for r in observed["records"]}
    declared_mode = observed['configuration'].get('code_mode', 'plain')
    if declared_mode not in {'plain', 'atomic_code'}:
        raise ValueError('Unknown parser-run representation mode')
    for r in observed['records']:
        if not r.get('error') and r.get('representation', {'mode': 'plain'}).get('mode') != declared_mode:
            raise ValueError('Parser run and record representation modes differ')
    started = time.perf_counter()
    records = [process_record(item, by_id[item["id"]]) for item in inputs]
    return {"schema_version": SCHEMA, "schema_status": "local_proposal_not_OCaml_importer_verified",
            "rule_version": RULE_VERSION,
            "artifact_type": "automatic_rule_candidates_development_not_gold",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "rule_files_sha256": {p.name: digest_file(p) for p in (HERE / "extract.py", HERE / 'code_input.py', HERE / 'local_qualifiers.py', HERE / 'semantic_bindings.py', HERE / 'purpose.py', HERE / 'only_pp.py', Path(__file__))},
            "parser_run": {k: v for k, v in observed.items() if k != "records"},
            "parser_provenance_note": "Metadata presence/format and record alignment checked; saved model hashes identify the prior run. Replay does not reload or independently reproduce the parser models.",
            "offset_convention": "Unicode code points in input.text, zero-based half-open; not UTF-8 bytes or original-file offsets",
            "extract_seconds_total": time.perf_counter() - started,
            "counts": {"inputs": len(records),
                       "runtime": dict(Counter(r["runtime_status"] for r in records)),
                       "candidate_status": dict(Counter(r["candidate"]["status"] for r in records if r["candidate"])),
                       "note": "Processing counts only, not accuracy, retention, overclaim rate, or independent evaluation."},
            "evaluation_denominator_policy": "Retain every input, including partial/unsupported/errors. scope_ablation_eligible only pairs full-vs-no-scope representations; it must not filter the evaluation dataset or another baseline.",
            "records": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--observed", type=Path, help="Replay an existing parse with exact input-file hash")
    parser.add_argument("--save-observed", type=Path, help="Save new local parse separately")
    parser.add_argument("--models", type=Path, default=LIBRI / "models" / "stanza")
    parser.add_argument('--code-mode', choices=['plain', 'atomic_code'], default=None,
                        help='New runs default to plain; replay infers saved mode unless explicitly checked')
    args = parser.parse_args()
    if args.observed and args.save_observed:
        parser.error("Choose replay or a new parser run, not both")
    if args.output.exists() or (args.save_observed and args.save_observed.exists()):
        parser.error("Output exists; choose a new path to preserve run evidence")
    if args.save_observed and args.save_observed.resolve() == args.output.resolve():
        parser.error("Parser output and candidate output need different paths")
    inputs = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(inputs, list) or not inputs:
        parser.error("Input must be a nonempty list of records")
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] for item in inputs):
        parser.error("Each input record needs a nonempty string ID")
    if len({item["id"] for item in inputs}) != len(inputs):
        parser.error("Duplicate input IDs; rejected before running the parser")
    if args.observed:
        observed = json.loads(args.observed.read_text(encoding="utf-8"))
        if observed["inputs_sha256"] != digest_file(args.input):
            parser.error("Frozen parse input-file hash mismatch")
        if args.code_mode and args.code_mode != observed['configuration'].get('code_mode', 'plain'):
            parser.error('Requested representation differs from saved parser run')
    else:
        observed = parse_locally(inputs, args.input, args.models, args.code_mode or 'plain')
        if args.save_observed:
            write_new(args.save_observed, observed)
    bundle = build_bundle(inputs, observed)
    bundle["input_file_sha256"] = digest_file(args.input)
    if args.observed:
        bundle["observed_file_sha256"] = digest_file(args.observed)
    elif args.save_observed:
        bundle["observed_file_sha256"] = digest_file(args.save_observed)
    write_new(args.output, bundle)
    print(json.dumps(bundle["counts"], ensure_ascii=False, indent=2))
    print("Saved:", args.output)


if __name__ == "__main__":
    main()
