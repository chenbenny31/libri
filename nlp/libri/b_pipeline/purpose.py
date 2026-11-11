"""Bounded use-object infinitival purpose overlays over source-backed syntax.

This indexes a goal without asserting its occurrence, success, controller, or
complete scope. An object-attached infinitive retains attachment uncertainty.
No source IDs, reference annotations, parser repair, or recursive event creation.
"""
from collections import defaultdict
from .only_pp import exclusive_pp_scopes


def purpose_spec(words, root_head, code_mentions, evidence):
    """Return one nested evidence specification, or abstain.

    ``evidence`` is the extractor's aligned evidence builder. It must retain all
    selected source words, including punctuation; this helper never slices raw
    text or synthesizes roles. The caller supplies an actual active main frame.
    """
    by = {w['id']: w for w in words}
    children = defaultdict(list)
    for word in words:
        children[word['head']].append(word)
    lemma = lambda w: (w.get('lemma') or w['text']).lower()
    feats = lambda w: set((w.get('feats') or '').split('|'))
    root = by[root_head]
    if root['head'] != 0 or root['upos'] != 'VERB' or lemma(root) != 'use':
        return None
    if ('Voice=Pass' in feats(root) or any(w['deprel'] in {'aux:pass', 'nsubj:pass', 'cop'}
                                         for w in children[root_head])):
        return None
    objects = [w for w in children[root_head] if w['deprel'] == 'obj']
    if len(objects) != 1 or objects[0]['upos'] not in {'NOUN', 'PROPN', 'PRON'}:
        return None
    tool = objects[0]
    code_ids = {i for mention in code_mentions for i in mention.get('parser_word_ids', [])}
    if root_head in code_ids:
        return None

    def subtree(wid):
        return [wid] + [i for child in children[wid] for i in subtree(child['id'])]

    def infinitive(word):
        return word['upos'] == 'VERB' and 'VerbForm=Inf' in feats(word)

    # Competing entrances are counted before marker/subject/passive gating. A
    # malformed second candidate is not permission to silently pick the first.
    entrances = [w for w in words if infinitive(w) and
                 ((w['head'] == root_head and w['deprel'] in {'advcl', 'xcomp'}) or
                  (w['head'] == tool['id'] and w['deprel'] == 'acl'))]
    if len(entrances) != 1:
        return None
    goal = entrances[0]
    marks = [w for w in children[goal['id']] if w['deprel'] == 'mark']
    if (len(marks) != 1 or lemma(marks[0]) != 'to' or marks[0]['upos'] != 'PART'
            or children[marks[0]['id']]):
        return None
    marker = marks[0]
    # Multiword purpose markers are outside this single-to family even if a
    # parser happens to attach their extra words elsewhere in the clause.
    before = [lemma(by[i]) for i in range(max(1, marker['id'] - 2), marker['id'])]
    if before == ['in', 'order']:
        return None
    # A for-NP between the tool and infinitive can supply an overt embedded
    # controller even when the parser attaches it to the tool or main use.
    # Abstain rather than silently omit that controller or call it an actor.
    for noun in words:
        if (noun['head'] not in {root_head, tool['id']} or
                noun['deprel'].split(':')[0] not in {'obl', 'nmod'}):
            continue
        cases = [w for w in children[noun['id']] if w['deprel'] == 'case' and lemma(w) == 'for']
        if any(tool['id'] < case['id'] < noun['id'] < marker['id'] for case in cases):
            return None
    # Adjacent focus cues also occur in unintended-result readings (used X
    # only to discover ...). Preserve them even if attached outside the goal,
    # and withhold a supported purpose relation instead of guessing intent.
    marker_modifiers = []
    previous = marker['id'] - 1
    while previous in by and lemma(by[previous]) in {'only', 'just', 'merely'}:
        marker_modifiers.append(previous)
        previous -= 1
    conjuncts = [w for w in children[goal['id']] if w['deprel'] == 'conj']
    actions = [goal] + conjuncts
    action_ids = {w['id'] for w in actions}
    goal_ids = set(subtree(goal['id']))
    if code_ids & action_ids:
        return None
    # No recursive control or embedded infinitive inference. Preserve the old
    # generic evidence when this finite overlay abstains.
    if any(infinitive(by[i]) and i not in action_ids for i in goal_ids):
        return None
    for action in actions:
        ach = children[action['id']]
        if (not infinitive(action) or 'Voice=Pass' in feats(action)
                or any(w['deprel'].split(':')[0] in {'nsubj', 'csubj', 'cop'} or
                       w['deprel'] == 'aux:pass' for w in ach)):
            return None
        if action is not goal:
            cc = [w for w in ach if w['deprel'] == 'cc']
            if (len(cc) != 1 or cc[0]['upos'] != 'CCONJ' or lemma(cc[0]) not in {'and', 'or'}
                    or children[cc[0]['id']] or any(w['deprel'] in {'conj', 'mark'} for w in ach)):
                return None

    # Only exact source attachment paths are recorded. In particular an acl is
    # not silently retargeted from the object to the use event.
    path = []
    word = goal
    while word['id'] != root_head:
        path.append({'word_id': word['id'], 'deprel': word['deprel'],
                     'governor_word_id': word['head']})
        word = by[word['head']]
    aid = {w['id']: f'pa{i + 1}' for i, w in enumerate(actions)}
    nested = []
    for action in actions:
        wid = action['id']
        local_ids = set(subtree(wid))
        for other in conjuncts:
            if other['id'] != wid:
                local_ids.difference_update(subtree(other['id']))
        direct_objects = [w for w in children[wid] if w['deprel'] == 'obj']
        limitations = []
        covered_limit_ids = set()
        # Top-down depth order makes each complete limitation subtree appear
        # once (e.g. only-from-index, not separate duplicate only/from nodes).
        def depth(i):
            d = 0
            while i != wid:
                i = by[i]['head']; d += 1
            return d
        for i in sorted(local_ids - {wid}, key=lambda i: (depth(i), i)):
            word = by[i]
            if i in covered_limit_ids:
                continue
            rel = word['deprel']
            limitation = (rel.split(':')[0] in {'advmod', 'neg', 'obl', 'nmod', 'advcl', 'ccomp', 'xcomp', 'acl'}
                          or (rel == 'det' and lemma(word) not in {'a', 'an', 'the'}))
            if limitation:
                ids = set(subtree(i)) & local_ids
                limitations.append({'head_word_id': i, 'syntactic_governor_word_id': word['head'],
                                    'syntactic_relation': rel, 'evidence': evidence(ids),
                                    'interpretation_status': 'unresolved'})
                covered_limit_ids.update(ids)
        coord = None
        if action is not goal:
            cc = next(w for w in children[wid] if w['deprel'] == 'cc')
            coord = {'governor_action_id': aid[goal['id']],
                     'syntactic_governor_word_id': goal['id'], 'cue': evidence([cc['id']])}
        nested.append({'id': aid[wid], 'head_word_id': wid,
                       'predicate': evidence([wid], lemma=lemma(action)),
                       'evidence': evidence(local_ids),
                       'objects': [{'head_word_id': w['id'], 'syntactic_relation': 'obj',
                                    'evidence': evidence(subtree(w['id']))} for w in direct_objects],
                       'coordination': coord, 'limitations': limitations,
                       'actor_status': 'unresolved', 'realization': 'not_asserted',
                       'interpretation_status': 'unresolved', 'is_independent_assertion': False})
    uncertain_attachment = goal['deprel'] == 'acl'
    result = {'cue_ids': sorted(marker_modifiers + [marker['id']]), 'parent_head_word_id': root_head,
            'tool_role': {'role': 'object', 'head_word_id': tool['id']},
            'syntactic_path': path, 'relation_hint': 'purpose',
            'relation_status': 'unresolved' if uncertain_attachment or marker_modifiers else 'supported',
            'purpose_head_word_id': goal['id'], 'purpose_evidence': evidence(goal_ids | set(marker_modifiers)),
            'actions': nested, 'realization': 'not_asserted', 'goal_success': 'not_asserted',
            'is_independent_assertion': False,
            'unresolved_reasons': ['purpose_composition_not_supported'] +
                (['object_infinitive_attachment_unresolved'] if uncertain_attachment else []) +
                (['modified_infinitive_marker'] if marker_modifiers else [])}
    scopes = exclusive_pp_scopes(words, nested, evidence)
    if scopes:
        result['exclusive_pp_scopes'] = scopes
    return result
