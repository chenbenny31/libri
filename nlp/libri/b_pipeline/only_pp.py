"""Finite only-from/to scope overlays inside existing non-asserted purposes.

The label is intentionally PP-generic: neither ``from`` nor ``to`` determines
an ontological source/destination/recipient type.  Basic dependency attachment
is preserved and never used to resolve shared scope across coordinated actions.
"""
from collections import defaultdict


def exclusive_pp_scopes(words, actions, evidence):
    """Interpret a narrow single-action PP focus, otherwise retain uncertainty.

    Discovery requires the literal adjacent only + from/to case pair within an
    existing purpose action.  Unsupported scope never invents a target.  The
    original action and limitation records are not changed by this function.
    """
    by = {w['id']: w for w in words}
    children = defaultdict(list)
    for w in words:
        children[w['head']].append(w)
    lemma = lambda w: (w.get('lemma') or w['text']).lower()
    by_head = {a['head_word_id']: a for a in actions}
    action_ids = [a['id'] for a in actions]
    root_ids = {w['id'] for w in words if w['head'] == 0}
    # A parser can promote a coordinated purpose verb to the main use clause.
    # One nested action then does not establish an unambiguous semantic scope.
    outer_coordination = any(w['head'] in root_ids and w['upos'] == 'VERB'
                             and w['deprel'] in {'conj', 'parataxis'}
                             and w['id'] not in by_head for w in words)
    ordinary_determiners = {'a', 'an', 'the'}
    focus_words = {'only', 'also', 'even', 'just', 'merely', 'except', 'rather', 'instead'}
    negative_words = {'not', "n't", 'never', 'neither', 'nor', 'no', 'without'}

    def subtree(wid):
        return {wid} | {i for c in children[wid] for i in subtree(c['id'])}

    def owning_action(wid):
        while wid and wid not in by_head:
            wid = by[wid]['head']
        return by_head.get(wid)

    def closed_nominal(wid, excluded=()):
        """Keep a complete simple NP; never truncate complex content to pass."""
        w = by[wid]
        if w['upos'] not in {'NOUN', 'PROPN'}:
            return False
        if any(part.startswith('NumType=') for part in (w.get('feats') or '').split('|')):
            return False
        for child in children[wid]:
            if child['id'] in excluded:
                continue
            rel = child['deprel']
            if rel == 'det' and lemma(child) in ordinary_determiners and not children[child['id']]:
                continue
            if rel == 'amod' and child['upos'] == 'ADJ' and not children[child['id']]:
                if any(part.startswith('NumType=') for part in (child.get('feats') or '').split('|')):
                    return False
                continue
            if rel == 'compound' and closed_nominal(child['id']):
                continue
            return False
        return True

    scopes = []
    for case in words:
        if case['deprel'] != 'case' or case['upos'] != 'ADP' or lemma(case) not in {'from', 'to'}:
            continue
        only = by.get(case['id'] - 1)
        if only is None or lemma(only) != 'only':
            continue
        pp = by.get(case['head'])
        if pp is None or pp['upos'] not in {'NOUN', 'PROPN', 'PRON', 'NUM', 'ADJ'}:
            continue
        action = owning_action(pp['id'])
        if action is None:
            continue
        local_ids = set(action['evidence']['word_ids'])
        # An outside only must never be borrowed across a clause/action boundary.
        # The evidence remains available in the original purpose if not local.
        if only['id'] not in local_ids or case['id'] not in local_ids:
            continue
        pp_ids = subtree(pp['id'])
        argument_ids = pp_ids - {case['id'], only['id']}
        if not argument_ids:
            continue
        focus_reasons = []
        scope_reasons = []
        # A nearby not/even/just changes the only construction even if the
        # parser attached that cue outside this action.  Keep its evidence,
        # withhold ordinary exclusivity, and never repair the original tree.
        adjacent_modifiers = set()
        previous = only['id'] - 1
        while previous in by and lemma(by[previous]) in negative_words | focus_words:
            adjacent_modifiers.add(previous)
            previous -= 1
        if adjacent_modifiers:
            focus_reasons.append('modified_only_sequence')
        cases = [w for w in children[pp['id']] if w['deprel'] == 'case']
        if (only['upos'] != 'ADV' or only['deprel'] != 'advmod' or children[only['id']]
                or only['head'] not in {pp['id'], action['head_word_id']}):
            focus_reasons.append('only_attachment_or_shape_unresolved')
        if len(cases) != 1 or children[case['id']]:
            focus_reasons.append('complex_case_marker')
        if not closed_nominal(pp['id'], {case['id'], only['id']}):
            focus_reasons.append('complex_PP_complement')

        local_words = [by[i] for i in sorted(local_ids)]
        if any(lemma(w) in negative_words or w['deprel'] == 'neg' for w in local_words):
            focus_reasons.append('child_negation_scope_unresolved')
        if any(w['id'] != only['id'] and lemma(w) in focus_words for w in local_words):
            focus_reasons.append('competing_or_modified_focus')
        if any(w['deprel'].split(':')[0] in {'advcl', 'ccomp', 'xcomp', 'acl', 'parataxis'}
               and w['id'] != action['head_word_id'] for w in local_words):
            scope_reasons.append('child_clause_scope_unresolved')
        if any(w['deprel'] in {'aux', 'aux:pass', 'cop'} for w in local_words):
            scope_reasons.append('child_auxiliary_scope_unresolved')
        if len(actions) != 1:
            scope_reasons.append('coordinated_action_scope_unresolved')
        if outer_coordination:
            scope_reasons.append('outer_clause_coordination_unresolved')
        if pp['head'] != action['head_word_id'] or pp['deprel'] != 'obl':
            scope_reasons.append('PP_not_direct_action_oblique')
        pp_markers = [w for w in local_words
                      if w['deprel'] == 'case' and w['upos'] == 'ADP' and lemma(w) in {'from', 'to'}]
        if len(pp_markers) != 1:
            scope_reasons.append('competing_from_to_PP_or_range')

        # A complex object can license a nominal modifier even when the parser
        # guessed an event attachment (copies of files only from an archive).
        # Do not repair that guess or silently discard the object-internal PP.
        objects = [w for w in children[action['head_word_id']] if w['deprel'] == 'obj']
        if len(objects) > 1 or any(not closed_nominal(w['id']) for w in objects):
            scope_reasons.append('complex_action_object_attachment_unresolved')

        focus_supported = not focus_reasons
        action_supported = focus_supported and not scope_reasons
        reasons = list(dict.fromkeys(focus_reasons + scope_reasons))
        scopes.append({
            'id': f'eps{len(scopes) + 1}',
            'kind': 'exclusive_prepositional_argument',
            'cue': evidence([only['id']]),
            'preposition': evidence([case['id']]),
            'literal_preposition': lemma(case),
            'argument': evidence(argument_ids),
            'evidence': evidence(pp_ids | {only['id']} | adjacent_modifiers),
            'context_evidence': evidence(local_ids | adjacent_modifiers),
            'pp_head_word_id': pp['id'],
            'syntactic_governor_word_id': pp['head'],
            'only_governor_word_id': only['head'],
            'syntactic_action_id': action['id'],
            'candidate_target_actions': action_ids if len(actions) != 1 else [action['id']],
            'confirmed_target_action': action['id'] if action_supported else None,
            'focus_status': 'supported' if focus_supported else 'unresolved',
            'action_scope_status': 'supported' if action_supported else 'unresolved',
            'interpretation_status': 'supported' if action_supported else 'unresolved',
            'semantic_role': 'not_determined',
            'alternative_set': 'contextual_PP_complement_alternatives',
            'scope_policy': 'within_parent_purpose_and_qualifiers',
            'realization': 'not_asserted',
            'is_independent_assertion': False,
            'unresolved_reasons': reasons,
        })
    return scopes
