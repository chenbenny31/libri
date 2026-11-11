"""Bounded semantic PP overlays; never mutate saved parser observations.

These lexical/path rules are development hypotheses, not general PP resolution.
code_mentions must come from code_input.code_mentions (verified source payload).
"""
from collections import defaultdict

KINDS = {'event_method', 'destination'}


def binding_specs(text, words, frame_head, code_mentions=()):
    by = {w['id']: w for w in words}
    children = defaultdict(list)
    for w in words:
        children[w['head']].append(w)
    lemma = lambda w: (w.get('lemma') or w['text']).lower()
    head = by.get(frame_head)
    if head is None or head['upos'] != 'VERB' or lemma(head) != 'add':
        return []
    if any(w['deprel'] in {'aux:pass', 'nsubj:pass'} for w in children[frame_head]):
        return []
    objects = [w for w in children[frame_head] if w['deprel'] == 'obj']
    if len(objects) != 1 or objects[0]['upos'] != 'NOUN':
        return []
    obj = objects[0]

    def subtree(wid):
        return [wid] + [i for w in children[wid] for i in subtree(w['id'])]

    def case(w, prep):
        cs = [c for c in children[w['id']] if c['deprel'] == 'case']
        if len(cs) != 1 or cs[0]['upos'] != 'ADP' or lemma(cs[0]) != prep or children[cs[0]['id']]:
            return None
        return cs[0]

    def det(w):
        return w['deprel'] == 'det' and lemma(w) in {'a', 'an', 'the'} and not children[w['id']]

    def simple_file(w, prep, excluded):
        c = case(w, prep)
        return (w['upos'] == 'NOUN' and lemma(w) == 'file' and c is not None
                and all(x == c or x['id'] == excluded or det(x) for x in children[w['id']]))

    def plain_changes(w, excluded=None):
        if w['upos'] != 'NOUN' or lemma(w) != 'change':
            return False
        for c in children[w['id']]:
            if c['id'] == excluded or det(c):
                continue
            if c['deprel'] == 'nmod:poss' and lemma(c) == 'your' and not children[c['id']]:
                continue
            if c['deprel'] == 'amod' and c['upos'] == 'ADJ' and not children[c['id']]:
                continue
            if c['deprel'] == 'case' and lemma(c) == 'of' and not children[c['id']]:
                continue
            return False
        return True

    def changes_object():
        if plain_changes(obj):
            return True
        if lemma(obj) != 'part':
            return False
        changes = [c for c in children[obj['id']] if c['deprel'] == 'nmod' and case(c, 'of')]
        return (len(changes) == 1 and plain_changes(changes[0])
                and all(c == changes[0] or det(c) or (c['deprel'] == 'amod' and lemma(c) == 'only'
                        and not children[c['id']]) for c in children[obj['id']]))

    def content_object(destination):
        # Permit the source's new OR changed syntax, without claiming to model OR.
        def file_modifier(w):
            if w['deprel'] != 'amod' or w['upos'] not in {'ADJ', 'VERB'}:
                return False
            if not children[w['id']]:
                return w['upos'] == 'ADJ' or 'VerbForm=Part' in (w.get('feats') or '').split('|')
            conj = children[w['id']]
            return (lemma(w) == 'new' and len(conj) == 1 and lemma(conj[0]) == 'change'
                    and conj[0]['deprel'] == 'conj' and conj[0]['text'].lower() == 'changed'
                    and len(children[conj[0]['id']]) == 1
                    and children[conj[0]['id']][0]['deprel'] == 'cc'
                    and lemma(children[conj[0]['id']][0]) == 'or'
                    and not children[children[conj[0]['id']][0]['id']])
        if lemma(obj) not in {'content', 'contents'}:
            return False
        for c in children[obj['id']]:
            if c == destination or det(c):
                continue
            of = case(c, 'of')
            if c['deprel'] != 'nmod' or lemma(c) != 'file' or c['upos'] != 'NOUN' or of is None:
                return False
            if not all(x == of or x == destination or det(x) or file_modifier(x) for x in children[c['id']]):
                return False
        return True

    def path(w):
        result = []
        while w['id'] != frame_head:
            result.append({'word_id': w['id'], 'deprel': w['deprel'], 'governor_word_id': w['head']})
            if w['head'] not in by:
                return []
            w = by[w['head']]
        return result

    specs = []
    descendants = set(subtree(frame_head))
    for w in words:
        if w['id'] not in descendants or w['deprel'] not in {'nmod', 'obl'}:
            continue
        prep = 'with' if case(w, 'with') else 'to' if case(w, 'to') else None
        if prep is None:
            continue
        c = case(w, prep)
        prev = by.get(c['id'] - 1)
        if prev is not None and lemma(prev) in {'not', 'only', 'except', 'even', 'rather'}:
            continue
        # Conservative refusal even when another same-preposition phrase has a
        # different scope; this is a coverage cost, not a silent tie breaker.
        if sum(case(by[i], prep) is not None for i in descendants) != 1:
            continue
        direct = w['head'] == frame_head and w['deprel'] == 'obl'
        source_ref = None
        if prep == 'with':
            mentions = [m for m in code_mentions if m['text'] == 'git add -p'
                        and m['single_parser_word'] and m['parser_word_ids'] == [w['id']]
                        and m['span'] == [w.get('start_char'), w.get('end_char')]
                        and text[slice(*m['span'])] == m['text']]
            if (len(mentions) != 1 or w['text'] != 'git add -p'
                    or w['upos'] not in {'NOUN', 'PROPN'} or children[w['id']] != [c]
                    or not changes_object()):
                continue
            governor = by[w['head']]
            nested = (w['deprel'] == 'nmod' and governor['deprel'] == 'obl'
                      and governor['head'] == frame_head and simple_file(governor, 'to', w['id']))
            if not (direct or nested):
                continue
            m = mentions[0]
            source_ref = {k: m[k] for k in ('id', 'span', 'raw_content_span')}
            kind, relation = 'event_method', 'means_of_adding'
        else:
            if (w['upos'] != 'NOUN' or lemma(w) != 'index'
                    or not all(x == c or det(x) for x in children[w['id']])
                    or not content_object(w)):
                continue
            governor = by[w['head']]
            nested = (w['deprel'] == 'nmod' and governor['deprel'] == 'nmod'
                      and lemma(governor) == 'file' and case(governor, 'of') is not None
                      and governor['head'] == obj['id'])
            if not (direct or nested):
                continue
            kind, relation = 'destination', 'destination_of_added_content'
        ids = sorted(subtree(w['id']))
        specs.append({'kind': kind, 'rule_id': 'bounded-' + kind.replace('_', '-') + '-v1',
                      'attachment_head_word_id': w['id'], 'relation': relation,
                      'cue_ids': ids, 'argument_ids': sorted(set(ids) - {c['id']}),
                      'original_attachment_path': path(w), 'semantic_retargeted': not direct,
                      'binding_basis': 'bounded_lexical_path_rule', 'source_code_ref': source_ref,
                      'argument_reference_status': 'source_mention_only',
                      'object_excluded_word_ids': ids if kind == 'destination' and nested else [],
                      'scope_policy': 'within_parent_frame', 'is_independent_assertion': False})
    return specs
