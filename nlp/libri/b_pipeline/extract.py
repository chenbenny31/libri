"""Finite dependency patterns over saved Stanza words.

These are document-statement candidates, never verified runtime facts. Unknown
attachments cause abstention from a complete candidate structure. Coverage is
relative to the implemented grammar, not a guarantee that all semantic issues
have been detected. No gold annotations or sample IDs are consulted.
"""

from collections import defaultdict
from copy import deepcopy
from .local_qualifiers import leaf_participle, local_qualifier_spec
from .semantic_bindings import binding_specs
from .purpose import purpose_spec


RULE_VERSION = "libri-b-local-0.11"
ORDINARY_DETERMINERS = {"a", "an", "the"}
MODALS = {"must", "should", "shall", "may", "might", "can", "could", "will", "would"}
# Restricted modal-force heuristics, not context-free linguistic guarantees.
# Flavor (deontic/epistemic/dynamic) is never claimed; even force/scope can need
# context. may/might/could/would stay unresolved: may not
# has a genuine negation-scope ambiguity, could/would are often past/irrealis.
MODAL_FORCE = {"must": "necessity", "shall": "necessity", "should": "weak_necessity",
               "will": "prediction", "can": "possibility"}
NEGATION_OUTSCOPES_MODAL = {"can"}  # cannot = Not(Possibility(p)), not Possibility(Not(p))
POSSIBILITY_MODALS = {"can", "could", "may", "might"}


def frame_grammar(words, head_id):
    """Tree-backed grammatical form, not a semantic-role or speech-act label."""
    by_id = {w["id"]: w for w in words}
    head = by_id[head_id]
    children = [w for w in words if w["head"] == head_id]
    feats = set((head.get("feats") or "").split("|"))
    normal = [w for w in children if w["deprel"] == "nsubj"]
    passive = [w for w in children if w["deprel"] == "nsubj:pass"]
    auxiliaries = [w for w in children if w["deprel"] == "aux:pass"]
    passive_hint = passive or auxiliaries or "Voice=Pass" in feats
    if passive_hint:
        if not (head["upos"] == "VERB" and len(passive) == len(auxiliaries) == 1 and not normal
                and ("VerbForm=Part" in feats or head.get("xpos") == "VBN")
                and (auxiliaries[0].get("lemma") or auxiliaries[0]["text"]).lower() == "be"
                and not any(w["head"] == auxiliaries[0]["id"] for w in words)):
            return None
    subjects = passive if passive_hint else normal
    imperative = (head["head"] == 0 and head["upos"] == "VERB" and "Mood=Imp" in feats
                  and not passive_hint and not any(w["deprel"].split(":")[0] in
                        {"nsubj", "csubj", "mark", "aux", "cop", "parataxis"} for w in children))
    return {"head_word_id": head_id,
            "voice": "passive" if passive_hint else ("active" if head["upos"] == "VERB" else "not_applicable"),
            "clause_form": "imperative" if imperative else "unspecified",
            "subject_status": "implicit_addressee" if imperative else ("explicit" if len(subjects) == 1 else "unresolved")}


def second_person_mention(words, head_id):
    head = next(w for w in words if w["id"] == head_id)
    return (head["upos"] == "PRON" and (head.get("lemma") or head["text"]).lower() == "you"
            and not any(w["head"] == head_id and w["deprel"] != "punct" for w in words))


def mention_metadata(words, head_id):
    by_id = {w["id"]: w for w in words}
    head = by_id[head_id]
    ids = {head_id}
    while True:
        extended = ids | {w["id"] for w in words if w["head"] in ids}
        if extended == ids:
            break
        ids = extended
    second_person = second_person_mention(words, head_id)
    kind = ("second_person" if second_person else "unresolved_pronoun" if head["upos"] == "PRON"
            else "nominal" if head["upos"] in {"NOUN", "PROPN"} else "other")
    deictic_words = {"this", "that", "these", "those", "following", "previous"}
    # Stanza lemmatizes the real adjective-like "following" as follow/VERB.
    # Preserve the source reference cue instead of relying on lemma alone.
    deictic = any((by_id[i].get("lemma") or by_id[i]["text"]).lower() in deictic_words
                 or (by_id[i]["deprel"] in {"amod", "det"} and by_id[i]["text"].lower() in deictic_words)
                 for i in ids)
    return {"head_word_id": head_id, "syntactic_relation": head["deprel"], "mention_type": kind,
            "reference_status": "document_addressee" if second_person else
                                "unresolved" if kind in {"unresolved_pronoun", "other"} or deictic else "source_mention_only"}


def pp_syntax_notes(text, words, head_id, case_ids, phrase_ids):
    """Describe a parser subtree's shape; this does not validate its meaning.

    Shared with the export validator so labels cannot be edited independently
    of the saved tree. Unit/development review remains separate from this
    serialization consistency check.
    """
    by_id = {w["id"]: w for w in words}
    children = defaultdict(list)
    for word in words:
        children[word["head"]].append(word)
    cases = [w for w in children[head_id] if w["deprel"] == "case"]
    notes = []
    if len(cases) != 1:
        notes.append("multiple_case_markers")
    if any(children[w["id"]] for w in cases):
        notes.append("modified_or_multiword_case")
    for wid in sorted(phrase_ids - case_ids - {head_id}):
        word = by_id[wid]
        rel = word["deprel"]
        if rel == "det" and (word.get("lemma") or word["text"]).lower() in ORDINARY_DETERMINERS and not children[wid]:
            continue
        if rel in {"compound", "amod"} and word["upos"] in {"NOUN", "PROPN", "ADJ"}:
            continue
        start, end = word.get("start_char"), word.get("end_char")
        if (rel == "punct" and word["text"] == "-" and isinstance(start, int) and isinstance(end, int)
                and end == start + 1 and 0 < start < end < len(text)
                and text[start:end] == "-" and text[start - 1].isalnum() and text[end].isalnum()):
            continue
        notes.append(f"internal_attachment:{rel}:{word['upos']}")
    if any(w["deprel"] == "case" for w in children[by_id[head_id]["head"]]):
        notes.append("governor_is_prepositional_nominal")
    return list(dict.fromkeys(notes))


class _Extractor:
    def __init__(self, text, sentence, code_mentions=()):
        self.code_mentions = code_mentions
        self.text = text
        self.sentence = sentence
        self.words = sentence["words"]
        self.by_id = {}
        self.children = defaultdict(list)
        self.reasons = []
        self.reason_events = []
        self.frames = []
        self.qualifiers = []
        self.ambiguous = False
        if not self.words:
            raise ValueError("empty_sentence_words")
        for word in self.words:
            wid = word["id"]
            if not isinstance(wid, int) or isinstance(wid, bool) or wid <= 0 or wid in self.by_id:
                raise ValueError("invalid_or_duplicate_word_id")
            self.by_id[wid] = word
        if list(self.by_id) != list(range(1, len(self.words) + 1)):
            raise ValueError("word_ids_must_be_ordered_and_sentence_local")
        for word in self.words:
            head = word["head"]
            if not isinstance(head, int) or isinstance(head, bool) or head < 0:
                raise ValueError("invalid_head")
            if head and head not in self.by_id:
                raise ValueError("missing_head")
            self.children[head].append(word)
            seen = {word["id"]}
            current = head
            while current:
                if current in seen:
                    raise ValueError("dependency_cycle")
                if current not in self.by_id:
                    raise ValueError("missing_head")
                seen.add(current)
                current = self.by_id[current]["head"]
            self._word_span(word)
        roots = self.children[0]
        if len(roots) != 1 or roots[0]["deprel"] != "root":
            raise ValueError("expected_one_root")
        self.root = roots[0]
        self.sentence_evidence = self._sentence_evidence()
        if any(w["upos"] == "PUNCT" and not self.supported_punctuation(w) for w in self.words):
            self.reason("nondeclarative_or_complex_punctuation")

    def reason(self, value):
        # Keep events as well as deduplicated diagnostics: the same limitation
        # can independently occur in both the main and conditional clause.
        self.reason_events.append(value)
        if value not in self.reasons:
            self.reasons.append(value)

    def lemma(self, word):
        return (word.get("lemma") or word["text"]).lower()

    def supported_punctuation(self, word):
        """Allow '.'/',' and a literal intra-word short hyphen only."""
        if word["text"] in {".", ","}:
            return True
        if word["text"] != "-":
            return False
        start, end = word.get("start_char"), word.get("end_char")
        return (isinstance(start, int) and isinstance(end, int)
                and end == start + 1 and 0 < start < end < len(self.text)
                and self.text[start:end] == "-"
                and self.text[start - 1].isalnum() and self.text[end].isalnum())

    def simple_default_attachment(self, head, case_word, relation):
        """Restrict default scope to an unmodified, directly attached 'by default'."""
        if relation != "obl":
            return False
        content_children = [w for w in self.children[head["id"]] if w["deprel"] != "punct"]
        if content_children != [case_word] or self.children[case_word["id"]]:
            return False
        start, _ = self._word_span(case_word)
        _, end = self._word_span(head)
        if self.text[start:end].lower().split() != ["by", "default"]:
            return False
        # Even if the parser attaches 'not'/'only' to the verb, a contiguous
        # 'not by default'/'only by default' is outside this local scope rule.
        previous = self.by_id.get(case_word["id"] - 1)
        if previous is not None and self.lemma(previous) in {"not", "only"}:
            _, previous_end = self._word_span(previous)
            gap = self.text[previous_end:start]
            if previous_end <= start and (not gap or gap.isspace()):
                return False
        return True

    def _check_span(self, start, end):
        if (not isinstance(start, int) or isinstance(start, bool)
                or not isinstance(end, int) or isinstance(end, bool)
                or not 0 <= start < end <= len(self.text)):
            raise ValueError("invalid_character_span")
        return start, end

    def _word_span(self, word):
        start, end = word.get("start_char"), word.get("end_char")
        if start is not None and end is not None:
            start, end = self._check_span(start, end)
            if self.text[start:end] != word["text"]:
                raise ValueError("word_text_alignment_mismatch")
            return start, end
        for token in self.sentence.get("tokens", []):
            if word["id"] in token.get("word_ids", []):
                span = self._check_span(token["start_char"], token["end_char"])
                if self.text[slice(*span)] != token["text"]:
                    raise ValueError("token_text_alignment_mismatch")
                self.reason(f"coarse_token_alignment:word_{word['id']}")
                return span
        raise ValueError("word_without_alignable_source")

    def _sentence_evidence(self):
        bounds = [self._word_span(w) for w in self.words]
        start, end = min(s for s, _ in bounds), max(e for _, e in bounds)
        surface = self.text[start:end]
        if surface != self.sentence["text"]:
            raise ValueError("sentence_text_alignment_mismatch")
        return {"text": surface, "spans": [[start, end]], "word_ids": list(self.by_id)}

    def evidence(self, ids, lemma=None):
        ids = sorted(set(ids))
        spans = []
        for start, end in sorted(set(self._word_span(self.by_id[i]) for i in ids)):
            if spans and (start <= spans[-1][1] or self.text[spans[-1][1]:start].isspace()):
                spans[-1][1] = max(end, spans[-1][1])
            else:
                spans.append([start, end])
        result = {"text": " ".join(self.text[s:e] for s, e in spans),
                  "spans": spans, "word_ids": ids}
        if lemma is not None:
            result["lemma"] = lemma
        return result

    def subtree(self, wid):
        result = [wid]
        for child in self.children[wid]:
            result.extend(self.subtree(child["id"]))
        return result

    def add_qualifier(self, kind, ids, target, supported=False, **extra):
        item = {"id": f"q{len(self.qualifiers) + 1}", "kind": kind,
                "cue": self.evidence(ids), "target": target,
                "interpretation_status": "supported" if supported else "unresolved"}
        item.update(extra)
        self.qualifiers.append(item)
        return item["id"]

    def qualifier(self, qid):
        return next(q for q in self.qualifiers if q["id"] == qid)

    def resolve_modal(self, modal_cues, negation_id):
        """Bind one modal's lexical force and its conventional scope with not.

        Returns the modal lemma when bound, else None with reasons recorded.
        Flavor is fixed to not_determined: B never claims a rule vs. inference.
        """
        if not modal_cues:
            return None
        if len(modal_cues) > 1:
            for _, word in modal_cues:
                self.reason(f"modal_interpretation_unresolved:{self.lemma(word)}")
            self.reason("multiple_modal_cues")
            return None
        qid, word = modal_cues[0]
        lemma = self.lemma(word)
        if lemma not in MODAL_FORCE:
            return None
        if negation_id is not None and negation_id != word["id"] + 1:
            # e.g. 'must always not' or a parser-moved not: relative scope unknown.
            self.reason(f"modal_interpretation_unresolved:{lemma}")
            self.reason("modal_negation_order_unresolved")
            return None
        qualifier = self.qualifier(qid)
        qualifier["interpretation_status"] = "supported"
        qualifier["modal_force"] = MODAL_FORCE[lemma]
        qualifier["modal_flavor"] = "not_determined"
        if negation_id is not None:
            qualifier["negation_scope"] = ("negation_over_modal" if lemma in NEGATION_OUTSCOPES_MODAL
                                           else "modal_over_negation")
        return lemma

    def noun_phrase(self, head, role, skip_ids=()):
        if head["upos"] not in {"NOUN", "PROPN"} and not second_person_mention(self.words, head["id"]):
            self.reason(f"unresolved_{role}_head:{head['upos']}")
        if head["upos"] in {"NOUN", "PROPN"} and mention_metadata(self.words, head["id"])["reference_status"] == "unresolved":
            self.reason(f"unresolved_{role}_reference")

        excluded = {i for wid in skip_ids for i in self.subtree(wid)}

        def collect(word):
            ids = [word["id"]]
            for child in self.children[word["id"]]:
                if child['id'] in skip_ids:
                    continue
                rel = child["deprel"]
                if rel == "det" and self.lemma(child) in ORDINARY_DETERMINERS and not self.children[child["id"]]:
                    continue
                if rel in {"compound", "amod"}:
                    ids.extend(collect(child))
                elif rel == "nmod:poss" and self.lemma(child) == "your" and child["upos"] == "PRON" and not self.children[child["id"]]:
                    ids.append(child["id"])
                elif rel == "nmod" and self.closed_of_phrase(child, skip_ids):
                    ids.extend(i for i in self.subtree(child["id"]) if i not in excluded)
                elif rel == "punct":
                    # Preserve source punctuation; quotes can change assertion status.
                    if child["text"] in {'"', "'", "“", "”", "‘", "’"}:
                        self.reason(f"quoted_{role}_phrase")
                    ids.append(child["id"])
                else:
                    self.reason(f"unmodeled_{role}_attachment:{rel}")
                    if rel.startswith("conj"):
                        self.reason(f"coordinated_{role}")
                    ids.extend(i for i in self.subtree(child["id"]) if i not in excluded)
            return ids

        return self.evidence(collect(head))

    def closed_of_phrase(self, head, skip_ids=()):
        """Only a nominal of-subtree with no unknown PP/quantifier/clause/conj."""
        def closed(word, require_of=False):
            if not require_of and leaf_participle(self.words, word):
                return True
            if word["upos"] not in {"NOUN", "PROPN", "ADJ"}:
                return False
            children = [c for c in self.children[word["id"]] if c["id"] not in skip_ids]
            cases = [c for c in children if c["deprel"] == "case"]
            if require_of:
                if (word["upos"] not in {"NOUN", "PROPN"} or len(cases) != 1
                        or self.lemma(cases[0]) != "of" or cases[0]["upos"] != "ADP"
                        or self.children[cases[0]["id"]]):
                    return False
            elif cases:
                return False
            for child in children:
                rel = child["deprel"]
                if require_of and child in cases:
                    continue
                if rel == "det" and self.lemma(child) in ORDINARY_DETERMINERS and not self.children[child["id"]]:
                    continue
                if rel == "nmod:poss" and self.lemma(child) == "your" and child["upos"] == "PRON" and not self.children[child["id"]]:
                    continue
                if rel in {"compound", "amod"} and closed(child):
                    continue
                if rel == "nmod" and closed(child, True):
                    continue
                return False
            return True
        return closed(head, True)

    def prepositional_candidates(self):
        """Addressable parser observations, never resolved semantic arguments.

        Keep these separate from qualifiers: an nmod governor can be a noun,
        whereas a qualifier target denotes a frame. In particular, neither
        preposition spelling nor frame_context establishes a semantic role.
        """
        result = []
        for head in self.words:
            hid = head["id"]
            if head["upos"] not in {"NOUN", "PROPN"} or head["deprel"] not in {"obl", "nmod"}:
                continue
            cases = [w for w in self.children[hid] if w["deprel"] == "case"]
            if not any(w["upos"] == "ADP" and self.lemma(w) in {"with", "to"} for w in cases):
                continue
            case_ids = {i for w in cases for i in self.subtree(w["id"])}
            phrase_ids = set(self.subtree(hid))
            object_ids = phrase_ids - case_ids
            notes = pp_syntax_notes(self.text, self.words, hid, case_ids, phrase_ids)
            governor = self.by_id[head["head"]]
            contexts = [f["id"] for f in self.frames if governor["id"] in f["predicate"]["word_ids"]]
            result.append({
                "id": f"p{len(result) + 1}",
                "preposition": self.evidence(case_ids),
                "object": self.evidence(object_ids),
                "phrase": self.evidence(phrase_ids),
                "nominal_head": self.evidence([hid]),
                "parser_attachment": {"deprel": head["deprel"], "governor": self.evidence([governor["id"]])},
                "frame_context": contexts[0] if len(contexts) == 1 else None,
                "syntax_pattern": "complex_nominal" if notes else "simple_nominal",
                "syntax_notes": notes,
                "boundary_status": "parser_subtree_not_validated",
                "semantic_role": "not_determined", "semantic_binding": "unresolved",
                "is_independent_assertion": False,
            })
        return result

    def frame(self, head, fid, skip_ids=(), allowed_marker=None):
        children = self.children[head["id"]]
        grammar = frame_grammar(self.words, head["id"])
        if grammar is None:
            self.reason("passive_shape_not_supported")
            return None
        copulas = [w for w in children if w["deprel"] == "cop"]
        if head["upos"] == "VERB":
            kind = "event"
        elif head["upos"] == "ADJ" and len(copulas) == 1 and self.lemma(copulas[0]) == "be":
            kind = "state"
        else:
            self.reason(f"unsupported_predicate:{head['upos']}")
            return None

        subject_relation = "nsubj:pass" if grammar["voice"] == "passive" else "nsubj"
        subjects = [w for w in children if w["deprel"] == subject_relation]
        if not subjects and any(w["deprel"] == "parataxis" for w in children):
            # This pattern can come from command-name misparses or legitimate
            # imperative clauses. Neither is supported by the explicit-subject
            # grammar: withhold the frame without repairing the parser tree.
            self.reason("missing_explicit_subject")
            self.reason("subjectless_parataxis_not_supported")
            return None
        objects = [w for w in children if w["deprel"] == "obj"]
        pred_ids = [head["id"]]
        pred_lemma = self.lemma(head)
        consumed = set(skip_ids)
        if pred_lemma == "take" and any(self.lemma(w) == "effect" for w in objects):
            if (len(objects) == 1 and objects[0]["upos"] == "NOUN"
                    and objects[0]["text"].lower() == "effect"
                    and objects[0]["id"] == head["id"] + 1
                    and not any(w["deprel"] != "punct" for w in self.children[objects[0]["id"]])):
                pred_ids.append(objects[0]["id"])
                consumed.add(objects[0]["id"])
                pred_lemma = "take effect"
                objects = []
            else:
                self.reason("unsupported_take_effect_variant")
                return None

        bindings = binding_specs(self.text, self.words, head["id"], self.code_mentions)
        consumed.update(s["attachment_head_word_id"] for s in bindings if not s["semantic_retargeted"])
        bound_exclusions = [s["attachment_head_word_id"] for s in bindings if s["object_excluded_word_ids"]]
        nested_specs = []
        if len(objects)==1:
            for child in self.children[objects[0]['id']]:
                spec = local_qualifier_spec(self.text,self.words,head['id'],child['id'])
                if spec is not None and spec['kind']=='object_configuration':
                    nested_specs.append(spec)
        roles = []
        if len(subjects) == 1:
            roles.append({"role": "subject", "phrase": self.noun_phrase(subjects[0], "subject"),
                          **mention_metadata(self.words, subjects[0]["id"])})
            consumed.add(subjects[0]["id"])
        elif not subjects and grammar["subject_status"] != "implicit_addressee":
            self.reason("missing_explicit_subject")
        elif len(subjects) > 1:
            self.reason("multiple_subject_candidates")
            self.ambiguous = True
        if len(objects) == 1:
            roles.append({"role": "object", "phrase": self.noun_phrase(objects[0], "object",
                          skip_ids=[s['attachment_head_word_id'] for s in nested_specs] + bound_exclusions),
                          **mention_metadata(self.words, objects[0]["id"])})
            consumed.add(objects[0]["id"])
        elif len(objects) > 1:
            self.reason("multiple_object_candidates")
            self.ambiguous = True
        frame = {"id": fid, "kind": kind, **grammar,
                 "predicate": self.evidence(pred_ids, lemma=pred_lemma),
                 "roles": roles, "qualifier_ids": [], "is_independent_assertion": False}
        self.frames.append(frame)
        negations = []
        defaults = []
        modal_cues = []
        for child in children:
            cid, rel, lemma = child["id"], child["deprel"], self.lemma(child)
            if cid in consumed:
                continue
            if rel == "punct":
                if not self.supported_punctuation(child):
                    self.reason("nondeclarative_or_complex_punctuation")
                continue
            if rel == "mark" and lemma == allowed_marker and not self.children[cid]:
                continue
            if rel in {"advmod", "neg"} and lemma == "not" and not self.children[cid]:
                negations.append(cid)
                frame["qualifier_ids"].append(self.add_qualifier("negation", [cid], fid, True))
                continue
            if rel == "advmod" and lemma in {"successfully", "manually"} and not self.children[cid]:
                frame["qualifier_ids"].append(self.add_qualifier("manner", [cid], fid, True))
                continue
            if rel in {"aux", "aux:pass", "cop"} and not self.children[cid]:
                if rel == "aux:pass" and grammar["voice"] == "passive":
                    qid = self.add_qualifier("passive_auxiliary", [cid], fid, True)
                elif lemma in MODALS:
                    if lemma not in MODAL_FORCE:
                        self.reason(f"modal_interpretation_unresolved:{lemma}")
                    qid = self.add_qualifier("modal", [cid], fid)
                    modal_cues.append((qid, child))
                elif lemma in {"do", "be", "have"}:
                    qid = self.add_qualifier("grammatical_auxiliary", [cid], fid, True)
                else:
                    self.reason(f"unmodeled_auxiliary:{lemma}")
                    qid = self.add_qualifier("unmodeled", [cid], fid)
                frame["qualifier_ids"].append(qid)
                continue
            spec = local_qualifier_spec(self.text, self.words, head['id'], cid)
            if spec is not None:
                spec = dict(spec)
                kind, cue_ids, argument_ids = spec.pop('kind'), spec.pop('cue_ids'), spec.pop('argument_ids')
                qid = self.add_qualifier(kind, cue_ids, fid, True,
                                         argument=self.evidence(argument_ids), **spec)
                frame['qualifier_ids'].append(qid)
                continue
            case = [w for w in self.children[cid] if w["deprel"] == "case"]
            if rel.startswith("obl") and lemma == "default" and len(case) == 1 and self.lemma(case[0]) == "by":
                simple = self.simple_default_attachment(child, case[0], rel)
                cue_ids = [case[0]["id"], cid] if simple else self.subtree(cid)
                qid = self.add_qualifier("default", cue_ids, fid)
                defaults.append((qid, simple))
            else:
                self.reason(f"unmodeled_predicate_attachment:{rel}:{lemma}")
                qid = self.add_qualifier("unmodeled", self.subtree(cid), fid)
            frame["qualifier_ids"].append(qid)
        for spec in nested_specs + bindings:
            spec = dict(spec)
            kind, cue_ids, argument_ids = spec.pop('kind'), spec.pop('cue_ids'), spec.pop('argument_ids')
            frame['qualifier_ids'].append(self.add_qualifier(kind,cue_ids,fid,True,
                                            argument=self.evidence(argument_ids),**spec))
        negation = None
        if len(negations) == 1:
            negation = negations[0]
        elif len(negations) > 1:
            self.reason("multiple_negation_cues")
            self.ambiguous = True
        modal = self.resolve_modal(modal_cues, negation)
        structure = {"kind": "frame", "frame": fid}
        if negation is not None and modal not in NEGATION_OUTSCOPES_MODAL:
            structure = {"kind": "negation", "child": structure}
        if modal is not None:
            structure = {"kind": "modal", "force": MODAL_FORCE[modal], "child": structure}
            if negation is not None and modal in NEGATION_OUTSCOPES_MODAL:
                structure = {"kind": "negation", "child": structure}

        if defaults:
            # Scope remains local and outermost. An unresolved modal is
            # explicitly excluded, while any other unresolved content, a
            # conditional context, or multiple default attachments prevents
            # claiming even this narrow binding.
            other_problems = [r for r in self.reasons
                              if not r.startswith("modal_interpretation_unresolved:")]
            unresolved_modals = [(qid, w) for qid, w in modal_cues
                                 if self.qualifier(qid)["interpretation_status"] != "supported"]
            # With an unresolved possibility modal, Not may outscope it
            # (cannot/may not), so Default(Not(frame)) could misstate the claim.
            possibility_scope_unknown = negation is not None and any(
                self.lemma(w) in POSSIBILITY_MODALS for _, w in unresolved_modals)
            simple_local_default = (len(defaults) == 1 and defaults[0][1]
                                    and not skip_ids and allowed_marker is None
                                    and not other_problems and not possibility_scope_unknown)
            if simple_local_default:
                qid = defaults[0][0]
                qualifier = self.qualifier(qid)
                qualifier["interpretation_status"] = "supported"
                qualifier["scope_candidate"] = {"kind": "default", "child": deepcopy(structure)}
                qualifier["scope_candidate_is_complete_claim"] = False
                qualifier["scope_candidate_scope"] = ("local_frame_negation_and_modal" if modal is not None
                                                      else "local_frame_and_negation_only")
                qualifier["scope_candidate_excluded_qualifier_ids"] = [qid for qid, _ in unresolved_modals]
                structure = deepcopy(qualifier["scope_candidate"])
            else:
                self.reason("default_binding_unresolved")
        return structure

    def extract(self):
        root_children = self.children[self.root["id"]]
        clauses = [w for w in root_children if w["deprel"] == "advcl"]
        only = [w for w in root_children if w["deprel"] == "advmod" and self.lemma(w) == "only"]
        relation, condition, marker = None, None, None
        modifier_ids = []
        marker_problems = []
        skip_ids = []
        if len(clauses) == 1:
            clause = clauses[0]
            markers = [w for w in self.children[clause["id"]] if w["deprel"] == "mark"]
            if len(markers) == 1:
                marker = markers[0]
                marker_lemma = self.lemma(marker)
                if marker_lemma == "if":
                    relation, condition = "if", clause
                    # An adjacent focus/negative cue changes the relation's
                    # meaning even if the parser attaches it to the main verb.
                    # Preserve the surface cue, but do not reinterpret it as
                    # ordinary sufficient-condition if or necessity only-if.
                    previous_id = marker["id"] - 1
                    while previous_id in self.by_id and self.lemma(self.by_id[previous_id]) in {
                            "even", "only", "except", "just", "not"}:
                        modifier_ids.append(previous_id)
                        previous_id -= 1
                    if modifier_ids:
                        marker_problems.append("modified_if_marker_unresolved")
                    if self.children[marker["id"]]:
                        marker_problems.append("complex_if_marker_unresolved")
                elif (marker_lemma == "after" and len(only) == 1
                      and not self.children[marker["id"]]
                      and not self.children[only[0]["id"]]
                      and only[0]["id"] < self.root["id"] < clause["id"]):
                    relation, condition = "only_after", clause
                    skip_ids.append(only[0]["id"])
                if condition is not None:
                    skip_ids.append(condition["id"])
        elif len(clauses) > 1:
            self.reason("multiple_adverbial_clauses")
            self.ambiguous = True

        structure = self.frame(self.root, "f1", skip_ids=skip_ids)
        if condition is not None and structure is not None:
            condition_problem_start = len(self.reason_events)
            cond_structure = self.frame(condition, "f2", allowed_marker=self.lemma(marker))
            condition_problems = list(dict.fromkeys(self.reason_events[condition_problem_start:]))
            if cond_structure is None or marker_problems:
                for problem in marker_problems:
                    self.reason(problem)
                # Unlike interpreted if/only_after, this is an addressable
                # source fragment, not a semantic condition operator. Include
                # marker subtrees and adjacent modifiers, including ones whose
                # parser governor lies outside the conditional subtree.
                cue_ids = set(self.subtree(marker["id"])) | set(modifier_ids)
                if relation == "only_after":
                    cue_ids.add(only[0]["id"])
                # This is a source fragment, not a normalized proposition.
                # Keep internal hyphens, parentheses and any clause punctuation
                # assigned to the subtree; dropping punctuation can lose meaning.
                evidence_ids = set(self.subtree(condition["id"])) | cue_ids
                condition_frame = "f2" if any(f["id"] == "f2" for f in self.frames) else None
                qid = self.add_qualifier(
                    "unresolved_condition", cue_ids, "f1", False,
                    relation_hint=relation, condition_head_word_id=condition["id"],
                    condition_frame=condition_frame, condition_evidence=self.evidence(evidence_ids),
                    condition_structure=None,
                    condition_status="partial" if condition_frame is not None else "unsupported",
                    unresolved_reasons=list(dict.fromkeys(condition_problems + marker_problems)),
                    is_independent_assertion=False)
                self.frames[0]["qualifier_ids"].append(qid)
                structure = None
            elif cond_structure is not None:
                content_ids = [i for i in self.subtree(condition["id"])
                               if i != marker["id"] and self.by_id[i]["deprel"] != "punct"]
                cue_ids = [marker["id"]] + ([only[0]["id"]] if relation == "only_after" else [])
                condition_covered = not condition_problems
                # A new outer modal/default node must not hide main-frame not.
                # Negated only-after remains outside this grammar regardless
                # of whether negation is the root of the local structure.
                scope_unresolved = relation == "only_after" and any(
                    q["kind"] == "negation" and q["target"] == "f1"
                    for q in self.qualifiers)
                if scope_unresolved:
                    self.reason("negated_only_after_scope_unresolved")
                complete_cond_structure = cond_structure if condition_covered else None
                qid = self.add_qualifier(relation, cue_ids, "f1", condition_covered and not scope_unresolved,
                                         condition_frame="f2", condition_evidence=self.evidence(content_ids),
                                         condition_structure=complete_cond_structure,
                                         condition_status="pattern_covered" if condition_covered else "partial",
                                         unresolved_reasons=condition_problems +
                                         (["negated_only_after_scope_unresolved"] if scope_unresolved else []))
                self.frames[0]["qualifier_ids"].append(qid)
                structure = {"kind": "conditional", "relation": relation,
                             "condition": "f2", "condition_structure": complete_cond_structure,
                             "consequent": structure}
                if relation == "only_after":
                    structure["direction"] = "main_requires_condition"
                    structure["does_not_assert_condition_or_main_occurrence"] = True

        # Purpose is a source-backed local overlay, never an asserted child
        # event or a replacement for existing unmodeled evidence/scope.
        main = next((f for f in self.frames if f['id'] == 'f1'), None)
        if main is not None and main['voice'] == 'active':
            spec = purpose_spec(self.words, main['head_word_id'], self.code_mentions, self.evidence)
            if spec is not None and any(r['role'] == 'object' and
                    r['head_word_id'] == spec['tool_role']['head_word_id'] for r in main['roles']):
                spec = dict(spec)
                cue_ids = spec.pop('cue_ids')
                qid = self.add_qualifier('purpose', cue_ids, main['id'], False, **spec)
                main['qualifier_ids'].append(qid)
                self.reason('purpose_composition_not_supported')

        pp_candidates = self.prepositional_candidates()
        if self.ambiguous:
            status = "ambiguous"
        elif not self.frames:
            status = "unsupported"
        elif self.reasons:
            status = "partial"
        else:
            status = "pattern_covered"
        if status != "pattern_covered":
            structure = None
        return {"status": status, "reasons": self.reasons,
                "frames": self.frames, "qualifiers": self.qualifiers,
                "pp_candidates": pp_candidates,
                "candidate_structure": structure, "evidence": self.sentence_evidence,
                "assertion_policy": "source_required"}


def extract_sentence(text, sentence, code_mentions=()):
    """Extract one candidate from a validated Stanza sentence in paragraph text.

    Offsets are zero-based, half-open Unicode code points relative to text.
    Malformed trees or impossible source alignment raise ValueError. Runtime
    callers should record these separately from semantic abstention.
    """
    return _Extractor(text, sentence, code_mentions).extract()


def expected_unresolved_conditions(text, sentence, code_mentions=()):
    """Recompute the unresolved-condition contract from original annotations.

    Kept separate from the batch extraction entry point so a runtime extraction
    failure/replacement cannot also substitute the validator's expected value.
    This is an evidence/inventory check, not independent semantic evaluation.
    """
    return [q for q in _Extractor(text, sentence, code_mentions).extract()['qualifiers']
            if q['kind'] == 'unresolved_condition']


def expected_purpose_contract(text, sentence, code_mentions=()):
    """Recompute exact purpose inventory and expected top-level frame heads.

    Inventory comparison is structural validation, not independent NLP scoring.
    This private extraction path remains separate from the runtime entry point.
    """
    candidate = _Extractor(text, sentence, code_mentions).extract()
    return ([q for q in candidate['qualifiers'] if q['kind'] == 'purpose'],
            [(f['id'], f['head_word_id']) for f in candidate['frames']],
            candidate['qualifiers'])


def expected_role_phrase(text, sentence, frame_head, role_head, role_name, code_mentions=()):
    """Contract check for complete emitted NP evidence, not semantic proof."""
    extractor = _Extractor(text,sentence)
    skip_ids = []
    if role_name=='object':
        skip_ids.extend(s['attachment_head_word_id'] for s in binding_specs(text, sentence['words'], frame_head, code_mentions) if s['object_excluded_word_ids'])
        for child in extractor.children[role_head]:
            spec=local_qualifier_spec(text,sentence['words'],frame_head,child['id'])
            if spec is not None and spec['kind']=='object_configuration':
                skip_ids.append(child['id'])
    return extractor.noun_phrase(extractor.by_id[role_head],role_name,skip_ids=skip_ids)
