"""Finite local semantic patterns over unchanged parser trees.

Rules identify event-bound content, not independently true facts. No sample IDs,
source requirements, command-name dictionary, or parser repair are consulted.
"""
from collections import defaultdict

KINDS = {'frequency', 'temporal_before', 'result_status', 'accompanying_result', 'object_configuration'}
EVENT_NOUNS = {'commit', 'restart', 'reboot', 'deployment', 'shutdown', 'startup', 'installation', 'upgrade'}


def leaf_participle(words, word):
    return (word['upos'] == 'VERB' and word['deprel'] == 'amod'
            and ('VerbForm=Part' in (word.get('feats') or '').split('|') or word.get('xpos') == 'VBN')
            and not any(w['head'] == word['id'] for w in words))


def local_qualifier_spec(text, words, frame_head, attachment_head):
    by_id = {w['id']:w for w in words}
    children = defaultdict(list)
    for w in words: children[w['head']].append(w)
    lemma = lambda w:(w.get('lemma') or w['text']).lower()
    head, child = by_id.get(frame_head), by_id.get(attachment_head)
    if head is None or child is None or head['upos'] != 'VERB':
        return None
    objects=[c for c in children[frame_head] if c['deprel']=='obj']
    nominal = (len(objects)==1 and child['head']==objects[0]['id'] and child['deprel']=='nmod')
    if child['head']!=frame_head and not nominal:
        return None
    if child['deprel'] not in {'obl', 'obl:unmarked','nmod'} or child['upos'] not in {'NOUN','PROPN'}:
        return None
    def subtree(wid):
        return [wid] + [i for c in children[wid] for i in subtree(c['id'])]
    def closed(w, root_case=None, excluded=None):
        if leaf_participle(words,w): return True
        if w['upos'] not in {'NOUN','PROPN','ADJ'}: return False
        for c in children[w['id']]:
            if c['id']==excluded: continue
            if c['id'] == root_case: continue
            rel= c['deprel']
            if rel == 'det' and lemma(c) in {'a','an','the'} and not children[c['id']]: continue
            if rel in {'compound','amod'} and closed(c): continue
            if rel == 'nmod:poss' and lemma(c) == 'your' and not children[c['id']]: continue
            if rel == 'nmod':
                cases=[x for x in children[c['id']] if x['deprel']=='case']
                if len(cases)==1 and lemma(cases[0])=='of' and cases[0]['upos']=='ADP' and not children[cases[0]['id']] and closed(c,cases[0]['id']):
                    continue
            if rel=='punct' and c['text']=='-':
                a,b=c.get('start_char'),c.get('end_char')
                if type(a) is int and type(b) is int and 0<a<b<len(text) and b==a+1 and text[a-1].isalnum() and text[b].isalnum(): continue
            return False
        return True
    cases = [c for c in children[attachment_head] if c['deprel']=='case']
    ids = sorted(subtree(attachment_head))
    if not cases:
        sub = [by_id[i] for i in ids]
        if (not nominal and child['deprel']=='obl:unmarked' and lemma(child)=='time' and len(sub)==2
                and [w['text'].lower() for w in sub]==['multiple','times']
                and sub[0]['deprel']=='amod' and sub[0]['head']==attachment_head
                and not children[sub[0]['id']]):
            kind, relation, argument_ids, role = 'frequency','repeated_unspecified_count',ids,None
        else: return None
    else:
        if len(cases)!=1 or cases[0]['upos']!='ADP' or children[cases[0]['id']]: return None
        case=cases[0]; prep=lemma(case)
        if prep not in {'with','before'} or (child['deprel']!='obl' and not nominal): return None
        # A direct immediately preceding focus/negative cue can scope over the
        # whole PP even when the parser attached that cue to the verb.
        previous = by_id.get(case['id']-1)
        if previous is not None and lemma(previous) in {'not','only','except','even'}: return None
        if not closed(child,case['id']): return None
        hosts={frame_head} | ({objects[0]['id']} if len(objects)==1 else set())
        competing=[c for c in words if c['head'] in hosts and any(x['deprel']=='case' and lemma(x)==prep for x in children[c['id']])]
        if len(competing)!=1: return None
        argument_ids=sorted(set(ids)-{case['id']}); role=None
        if not nominal and prep=='before' and lemma(child) in EVENT_NOUNS:
            # Event head plus ordinary determiner only; e.g. not 'failed commit'.
            if any(c['id']!=case['id'] and not (c['deprel']=='det' and lemma(c) in {'a','an','the'} and not children[c['id']]) for c in children[attachment_head]): return None
            kind,relation='temporal_before','before_event'
        elif not nominal and prep=='with' and lemma(head)=='exit' and lemma(child)=='status':
            if objects or any(c['deprel'] in {'iobj','aux:pass','nsubj:pass'} for c in children[frame_head]): return None
            value=[by_id[i]['text'] for i in argument_ids if i!=attachment_head and by_id[i]['deprel']!='det']
            normalized=''.join(value).lower()
            if normalized not in {'zero','non-zero','nonzero'}: return None
            kind,relation='result_status','exit_status'
        elif not nominal and prep=='with' and lemma(head)=='fail' and lemma(child)=='list':
            if objects or any(c['deprel'] in {'iobj','aux:pass','nsubj:pass'} for c in children[frame_head]): return None
            of_children=[c for c in children[attachment_head] if c['deprel']=='nmod'
                         and any(x['deprel']=='case' and lemma(x)=='of' for x in children[c['id']])]
            if len(of_children)!=1: return None
            # No additional list modifiers whose scope this pattern does not model.
            if any(c not in of_children and c!=case and c['deprel']!='det' for c in children[attachment_head]): return None
            kind,relation='accompanying_result','failure_accompanied_by_list'
        elif prep=='with' and lemma(head) in {'start','create','run'} and lemma(child)=='policy':
            if len(objects)!=1 or lemma(objects[0])!='container' or any(c['deprel'] in {'aux:pass','nsubj:pass'} for c in children[frame_head]): return None
            # Reject coordinated/relativized object scopes, not merely PP complexity.
            if not closed(objects[0],excluded=attachment_head if nominal else None): return None
            mods=[c for c in children[attachment_head] if c!=case and c['deprel']!='det']
            restarts=[c for c in mods if lemma(c)=='restart' and c['deprel']=='compound' and not children[c['id']]]
            values=[c for c in mods if c not in restarts]
            if len(restarts)!=1 or len(values)!=1 or values[0]['deprel'] not in {'amod','compound'} or children[values[0]['id']]: return None
            if values[0]['upos'] not in {'NOUN','PROPN','ADJ'}: return None
            kind,relation='object_configuration','configuration_of_event_object'
            role={'role':'object','head_word_id':objects[0]['id']}
        else: return None
    return {'kind':kind, 'rule_id':'local-'+kind.replace('_','-')+'-v1',
            'attachment_path':'direct_object' if nominal else 'direct_event',
            'attachment_head_word_id':attachment_head, 'relation':relation,
            'cue_ids':ids, 'argument_ids':argument_ids, 'role_target':role,
            'scope_policy':'within_parent_frame', 'is_independent_assertion':False}
