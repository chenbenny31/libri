# Meaning retention on 32 development targets

The four methods used the same target spans, paragraph text and frozen plain-text Stanza parses. Each target has one or more source-based requirements. A requirement can concern an action, its participants, or the scope of a restriction. The data comes from four public Git and Docker documents. All 32 targets were used during development.

| Method key | Method | Requirements fully retained / 97 | Targets with all requirements retained / 32 |
| --- | --- | ---: | ---: |
| `v05_plain` | Earlier v0.5 extractor | 21 | 4 |
| `v11_plain` | Current v0.11 extractor | 33 | 5 |
| `svo_plain` | Root verb plus direct subject and object heads | 1 | 0 |
| `scope_removed_plain` | v0.11 with designated scope structures removed | 21 | 2 |

Each row in the [review file](meaning_review.json) has a target ID, the source revision, the original requirement and evidence, and a judgment label for each method. Join a target to the v0.11 replay using its ID. The labels are AI-assisted development judgments. The team's independent evaluation on other documents is pending.

* `retained` means the extracted fields express the required meaning, including the requested attachment or scope.
* `partial` means they express a correct structural part of that requirement.
* `text_only` means a localized model output preserves the words while the relation remains uninterpreted.
* `missing` means the requirement is absent from the extracted structures and localized evidence. Every record carries source text, so simply finding the words in the input does not earn credit.
* `wrong` records a contradictory interpretation the output explicitly commits to.

To reproduce the table from the archived judgments, run the following in this directory using Python 3.

```sh
python3 - <<'PY'
import json
from pathlib import Path

review = json.loads(Path("meaning_review.json").read_text())
for method in review["methods"]:
    total_retained = 0
    complete_targets = 0
    for record in review["records"]:
        retained = [
            req["development_reviews"][method]["judgment"] == "retained"
            for req in record["requirements"]
        ]
        total_retained += sum(retained)
        complete_targets += bool(retained) and all(retained)
    print(method, total_retained, complete_targets)
PY
```

The [runtime guide](../RUN.md) reproduces the current extractor's outputs. The four historical comparison results above are frozen. Dropping a scope node can also drop its nested action, and some qualifiers remain as unresolved text after ablation. These comparisons measure the contents of the representations. User-task success, formal agreement and LLM baseline results are pending.
