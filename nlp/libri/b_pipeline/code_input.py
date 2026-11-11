"""Source inline-code boundaries and optional atomic token preparation.

Finite renderer promoted from the frozen command-representation experiment.
Formatting is evidence of a surface unit, never a command/role semantic label.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


def sha(value):
    return hashlib.sha256(value).hexdigest()


def normalize(chars):
    result = []
    pending = None
    for char, index in chars:
        if char.isspace():
            if result and pending is None:
                pending = index
        else:
            if pending is not None:
                result.append((" ", pending))
            result.append((char, index))
            pending = None
    return "".join(c for c, _ in result), [i for _, i in result]


def prepare_raw(raw):
    # This finite experiment handles only the syntax present in selected
    # paragraphs: simple inline links, one leading list marker, single ticks.
    # It is deliberately not advertised as a general Markdown parser.
    if re.search(r'\\+`', raw):
        raise ValueError('Escaped backticks are outside the finite renderer')
    pairs, cursor = [], 0
    for match in re.finditer(r"\[([^\]]+)\]\([^\s)]+\)", raw):
        pairs.extend((raw[i], i) for i in range(cursor, match.start()))
        pairs.extend((raw[i], i) for i in range(match.start(1), match.end(1)))
        cursor = match.end()
    pairs.extend((raw[i], i) for i in range(cursor, len(raw)))
    text = "".join(c for c, _ in pairs)
    lead = re.match(r"^\s*[-*]\s+", text)
    if lead:
        pairs = pairs[lead.end():]
        text = "".join(c for c, _ in pairs)
    matches = list(re.finditer(r"`([^`]+)`", text))
    if text.count("`") != 2 * len(matches) or any("\n" in m.group(1) for m in matches):
        raise ValueError("Unsupported inline-code syntax; do not guess boundaries")
    delimiters = {i for m in matches for i in (m.start(), m.end() - 1)}
    plain, plain_map = normalize([p for i, p in enumerate(pairs) if i not in delimiters])
    marked, marked_map = normalize(pairs)
    codes = []
    for match in matches:
        low, high = pairs[match.start(1)][1], pairs[match.end(1) - 1][1] + 1
        def mapped(mapping, start, end):
            indexes = [i for i, r in enumerate(mapping) if start <= r < end]
            if not indexes or indexes != list(range(indexes[0], indexes[-1] + 1)):
                raise ValueError("Code mapping is empty or discontinuous")
            return [indexes[0], indexes[-1] + 1]
        pspan = mapped(plain_map, low, high)
        mspan = mapped(marked_map, low, high)
        delimiters_span = mapped(marked_map, pairs[match.start()][1], pairs[match.end() - 1][1] + 1)
        content = plain[slice(*pspan)]
        if content != marked[slice(*mspan)] or content != " ".join(raw[low:high].split()):
            raise ValueError("Code content changed during preparation")
        codes.append({"text": content, "raw_content_span": [low, high],
                      "plain_span": pspan, "marked_content_span": mspan,
                      "marked_span": delimiters_span,
                      "kind": "source_inline_code_not_semantic_command_label"})
    return {"plain": plain, "marked": marked, "plain_to_raw": plain_map,
            "marked_to_raw": marked_map, "codes": codes}


def token_plan(sentences, codes):
    """Merge only source-confirmed exact token intervals; never copy POS/heads."""
    intervals = sorted((tuple(c["plain_span"]) for c in codes))
    if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
        raise ValueError("Overlapping code intervals")
    consumed = set()
    planned = []
    for sentence in sentences:
        tokens = sentence["tokens"]
        result, pos = [], 0
        while pos < len(tokens):
            token = tokens[pos]
            start, end = token["start_char"], token["end_char"]
            overlap = [span for span in intervals if span[0] < end and start < span[1]]
            if overlap:
                if len(overlap) != 1 or overlap[0][0] != start:
                    raise ValueError("Code interval does not start on a token boundary")
                span = overlap[0]
                endpos = pos
                while endpos < len(tokens) and tokens[endpos]["end_char"] < span[1]:
                    endpos += 1
                if endpos >= len(tokens) or tokens[endpos]["end_char"] != span[1]:
                    raise ValueError("Code interval crosses sentence/token boundaries")
                result.append({"start_char": start, "end_char": span[1], "mwt": False,
                               "source_code": True})
                consumed.add(span)
                pos = endpos + 1
            else:
                result.append({"start_char": start, "end_char": end,
                               "mwt": len(token["word_ids"]) > 1, "source_code": False})
                pos += 1
        planned.append(result)
    if consumed != set(intervals):
        raise ValueError("Some source code interval was not represented by parser tokens")
    return planned


def document_from_plan(text, plan):
    from stanza.models.common.doc import Document
    data = []
    for sentence in plan:
        words = []
        for i, token in enumerate(sentence, 1):
            start, end = token["start_char"], token["end_char"]
            word = {"id": (i,), "text": text[start:end], "start_char": start, "end_char": end}
            if token["mwt"]:
                word["misc"] = "MWT=Yes"
            words.append(word)
        data.append(words)
    return Document(data, text=text)


def source_code_payload(text, raw):
    prepared = prepare_raw(raw)
    if prepared['plain'] != text:
        raise ValueError('Source renderer does not reproduce the complete input text')
    return {'version': 'libri-inline-code-0.1', 'raw_paragraph': raw,
            'raw_sha256': sha(raw.encode()), 'text_sha256': sha(text.encode()),
            'plain_to_raw': prepared['plain_to_raw'],
            'spans': [{'id': f'c{i}', 'text': c['text'], 'span': c['plain_span'],
                       'raw_content_span': c['raw_content_span']}
                      for i, c in enumerate(prepared['codes'], 1)],
            'meaning': 'source_inline_code_not_semantic_command_label'}


def with_source_code(item, raw):
    result = deepcopy(item)
    result['source_code'] = source_code_payload(item['text'], raw)
    validate_source_code(result)
    return result


def validate_source_code(item, required=False):
    payload = item.get('source_code')
    if payload is None:
        if required:
            raise ValueError('atomic_code requires explicit source_code, including a verified empty inventory')
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get('raw_paragraph'), str):
        raise ValueError('Invalid source_code object')
    expected = source_code_payload(item['text'], payload['raw_paragraph'])
    if json.dumps(payload, sort_keys=True, ensure_ascii=False) != json.dumps(expected, sort_keys=True, ensure_ascii=False):
        raise ValueError('Source-code spans/map/hash differ from source markup')
    source = item.get('source', {})
    if item.get('kind', '').startswith('real'):
        if not source.get('raw_paragraph_sha256') or not source.get('raw_paragraph_span'):
            raise ValueError('Real source_code requires source paragraph position and hash')
        span = source['raw_paragraph_span']
        if (not isinstance(span, list) or len(span) != 2 or any(type(n) is not int for n in span)
                or not 0 <= span[0] < span[1]):
            raise ValueError('Invalid real-source paragraph span')
        root = Path(__file__).resolve().parent.parent
        local = (root / source.get('file', '')).resolve()
        if local.is_relative_to(root) and local.is_file():
            raw_bytes = local.read_bytes()
            if sha(raw_bytes) != source.get('file_sha256'):
                raise ValueError('Source file hash mismatch for code markup')
            if raw_bytes.decode('utf-8')[slice(*span)] != payload['raw_paragraph']:
                raise ValueError('Code markup differs from local source paragraph slice')
    if source.get('raw_paragraph_sha256') and source['raw_paragraph_sha256'] != payload['raw_sha256']:
        raise ValueError('Code markup differs from declared source paragraph')
    return payload


def code_fingerprint(payload):
    return sha(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())


def plan_for_payload(sentences, payload):
    return token_plan(sentences, [{'plain_span': c['span']} for c in payload['spans']])


def validate_representation(item, parsed, validate_sentence):
    payload = validate_source_code(item)
    representation = parsed.get('representation', {'mode': 'plain'})
    mode = representation.get('mode')
    if mode not in {'plain', 'atomic_code'}:
        raise ValueError('Unknown saved parser representation')
    if mode == 'plain':
        if representation != {'mode': 'plain'}:
            raise ValueError('Unexpected plain representation fields')
        return mode
    payload = validate_source_code(item, required=True)
    required = {'mode', 'source_code_sha256', 'plain_sentences', 'token_plan'}
    if set(representation) != required or representation['source_code_sha256'] != code_fingerprint(payload):
        raise ValueError('Atomic source-code fingerprint or representation fields mismatch')
    plain = representation['plain_sentences']
    for s in plain:
        validate_sentence(item['text'], s)
    plan = plan_for_payload(plain, payload)
    if representation['token_plan'] != plan:
        raise ValueError('Atomic token plan differs from source boundaries and plain parse')
    actual = [[[t['start_char'], t['end_char']] for t in s['tokens']] for s in parsed['sentences']]
    expected = [[[t['start_char'], t['end_char']] for t in s] for s in plan]
    if actual != expected:
        raise ValueError('Atomic parse tokens do not match the declared merge plan')
    for sentence, planned in zip(parsed['sentences'], plan):
        for token, p in zip(sentence['tokens'], planned):
            if p['source_code'] and len(token['word_ids']) != 1:
                raise ValueError('Atomic code must remain exactly one parser word')
    return mode


def code_mentions(item, sentence):
    payload = validate_source_code(item)
    if payload is None:
        return []
    tokens = sentence['tokens']
    low, high = tokens[0]['start_char'], tokens[-1]['end_char']
    mentions = []
    for c in payload['spans']:
        start, end = c['span']
        if not start < high or not low < end:
            continue
        if not low <= start < end <= high:
            raise ValueError('Source code crosses the target sentence boundary')
        overlapping = [t for t in tokens if t['start_char'] < end and start < t['end_char']]
        ids = sorted({wid for t in overlapping for wid in t['word_ids']})
        mentions.append({**deepcopy(c), 'kind': 'source_inline_code',
                         'semantic_type': 'not_determined', 'is_independent_assertion': False,
                         'sentence_id': sentence['sentence_id'], 'parser_word_ids': ids,
                         'parser_token_spans': [[t['start_char'], t['end_char']] for t in overlapping],
                         'single_parser_word': len(overlapping) == 1 and len(ids) == 1
                             and [overlapping[0]['start_char'], overlapping[0]['end_char']] == c['span']})
    return mentions


def boundary_issues(candidate, mentions):
    issues = []
    def walk(value, path):
        if isinstance(value, dict):
            if 'spans' in value and 'word_ids' in value and 'text' in value:
                for c in mentions:
                    start, end = c['span']
                    overlap = any(a < end and start < b for a,b in value['spans'])
                    contains = any(a <= start and end <= b for a,b in value['spans'])
                    if overlap and not contains:
                        issues.append({'kind': 'code_fragment_in_candidate_evidence', 'path': path,
                                       'code_id': c['id'], 'full_code_text': c['text']})
            for key, val in value.items():
                walk(val, path + '/' + key)
        elif isinstance(value, list):
            for i, val in enumerate(value): walk(val, path + '/' + str(i))
    walk(candidate, '/candidate')
    for i, frame in enumerate(candidate['frames']):
        for c in mentions:
            if frame['head_word_id'] in c['parser_word_ids']:
                issues.append({'kind': 'source_code_used_as_predicate',
                               'path': f'/candidate/frames/{i}/predicate',
                               'code_id': c['id'], 'full_code_text': c['text']})
    return issues


def apply_boundary_guard(candidate, issues):
    if issues:
        for kind in sorted({i['kind'] for i in issues}):
            if kind not in candidate['reasons']: candidate['reasons'].append(kind)
        if candidate['status'] == 'pattern_covered': candidate['status'] = 'partial'
        candidate['candidate_structure'] = None


def main():
    """Enrich inputs from local frozen source snapshots; no parser or network."""
    import argparse
    from .run import LIBRI, provenance_check, write_new
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    rows = json.loads(args.input.read_text())
    enriched = []
    for item in rows:
        if provenance_check(item)['status'] != 'local_snapshot_hash_verified':
            raise ValueError('Preparation requires a verified local source snapshot')
        source = item['source']
        raw = (LIBRI / source['file']).read_text()[slice(*source['raw_paragraph_span'])]
        enriched.append(with_source_code(item, raw))
    write_new(args.output, enriched)
    print(f'Prepared source code boundaries for {len(enriched)} inputs; no parser run.')


if __name__ == '__main__': main()
