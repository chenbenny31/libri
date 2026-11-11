"""Observe Stanza annotations. This script does not extract semantic claims."""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform

HERE = Path(__file__).resolve().parent
LIBRI = HERE.parent.parent
# Keep model downloads and their cache inside this local project.
os.environ.setdefault("HF_HOME", str(LIBRI / "models" / "hf-cache"))

import stanza
import torch
from stanza.resources.common import DEFAULT_RESOURCES_VERSION

# Names checked against Stanza's resources_1.15.0.json.
PROCESSORS = {
    "tokenize": "ewt_nocharlm",
    "mwt": "ewt",
    "pos": "ewt_nocharlm",
    "lemma": "ewt_nocharlm",
    "depparse": "ewt_nocharlm",
}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def annotations(doc, text):
    """Keep tokens and syntactic words distinct, including their identifiers."""
    sentences = []
    for number, sentence in enumerate(doc.sentences, start=1):
        tokens = []
        for token in sentence.tokens:
            start, end = token.start_char, token.end_char
            if start is None or end is None or text[start:end] != token.text:
                raise ValueError(f"Token cannot be aligned: {token.text!r}")
            tokens.append({
                "id": list(token.id), "text": token.text,
                "start_char": start, "end_char": end,
                "word_ids": [word.id for word in token.words],
            })
        words = [
            {field: getattr(word, field) for field in (
                "id", "text", "lemma", "upos", "xpos", "feats", "head",
                "deprel", "start_char", "end_char",
            )}
            for word in sentence.words
        ]
        sentences.append({
            "sentence_id": number, "text": sentence.text,
            "tokens": tokens, "words": words,
        })
    return sentences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=HERE / "inputs.json")
    parser.add_argument("--output", type=Path, default=LIBRI / "runs" / "stanza-probe.json")
    parser.add_argument("--models", type=Path, default=LIBRI / "models" / "stanza")
    parser.add_argument("--download", action="store_true", help="Download selected models first")
    args = parser.parse_args()
    inputs = json.loads(args.input.read_text(encoding="utf-8"))
    if args.download:
        stanza.download("en", model_dir=str(args.models), package=None, processors=PROCESSORS)

    # After setup, the experiment runs locally without downloading anything.
    torch.set_num_threads(2)
    nlp = stanza.Pipeline(
        lang="en", dir=str(args.models), package=None, processors=PROCESSORS,
        use_gpu=False, download_method=None,
    )
    records = []
    for item in inputs:
        text = item["text"]
        doc = nlp(text)  # str -> Document -> sentences -> tokens and words
        records.append({
            "id": item["id"], "kind": item["kind"], "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "sentences": annotations(doc, text),
        })
        print(f"\n{item['id']}: {text}")
        for sentence in doc.sentences:
            for word in sentence.words:
                head_text = "ROOT" if word.head == 0 else sentence.words[word.head - 1].text
                print(f"{word.id:2} {word.text:12} lemma={word.lemma:12} "
                      f"upos={word.upos:5} head={word.head:2}({head_text}) {word.deprel}")

    model_files = sorted(args.models.glob("en/**/*.pt"))
    result = {
        "artifact_type": "observed_parser_annotations_not_semantic_extraction",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": {name: version(name) for name in ("stanza", "torch", "numpy")},
        "resources_version": DEFAULT_RESOURCES_VERSION,
        "resources_sha256": sha256(args.models / "resources.json"),
        "models": {str(path.relative_to(args.models)): sha256(path) for path in model_files},
        "configuration": {"language": "en", "processors": PROCESSORS, "device": "cpu", "threads": 2},
        "script_sha256": sha256(Path(__file__)), "inputs_sha256": sha256(args.input),
        "offset_convention": {
            "relative_to": "each record.text", "unit": "Unicode code points",
            "start": "zero-based inclusive", "end": "exclusive",
            "note": "Word offsets may be null for expanded words; parent token offsets remain available.",
        },
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nSaved {len(records)} observed parses to {args.output}")


if __name__ == "__main__":
    main()
