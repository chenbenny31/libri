# Libri Part B · run the Python extractor

The v0.11 extractor turns a target sentence and its surrounding paragraph into candidate actions, roles and qualifications with source evidence. Run the commands below **from the directory containing this file**. Each output filename must be new.

## Replay all 32 development targets

Use Python 3.11 or later. The offline path uses only the Python standard library and the saved Stanza annotations. This command runs the normal extractor CLI twice, in a temporary directory.

```sh
python3 check_replay.py
```

Expected final messages for the two batches are `old20: 20 records and stable metadata match` and `new12: 12 records and stable metadata match`. The check compares each input and parse file hash and a canonical SHA-256 of the complete output bundle against the archived v0.11 default results. The run timestamp and extraction duration vary. All 32 targets remain in the check, including partial, ambiguous and unsupported cases.

To keep a prediction file for OCaml import or scoring:

```sh
python3 -m libri.b_pipeline.run \
  --input fixtures/new12/inputs.json \
  --observed fixtures/new12/observed.plain.json \
  --output runs/new12.candidates.json
```

Use the same pair of filenames under `fixtures/old20/` to replay the original twenty targets. The bundled plain parses are the saved normal-tokenization stage of two prior parser runs that also tested an alternate representation. Each file carries the original model hashes and the matching input hash.

## Read the output

Use `records[].id` to pair records across methods. The paragraph is `input.text`, and `target_span` uses zero-based, end-exclusive Unicode code points into that paragraph. Read the primary prediction from `candidate`. It includes `frames`, `qualifiers`, `candidate_structure`, reasons and the target sentence evidence. Some qualifiers preserve text with `interpretation_status=unresolved`. Carry those qualifiers and the source when displaying an action. `runtime_status` reports processing, and `candidate.status` describes rule coverage; a successfully processed record can have no frames. Diagnostic baselines live under `variants`.

In `nr-docker-patch-only-11`, for example, reading only the main action loses the source clause limiting support to patch releases. The clause is preserved in the third qualifier as unresolved. In `docker-restart-prerequisite-001` the rule binds successful container startup as a prerequisite of a restart policy taking effect.

## Parse new English text

The live path additionally needs Stanza and its English model files. The recorded environment used Python 3.12.14, stanza 1.15.0, torch 2.14.1 and numpy 2.5.4 on CPU. In a compatible Python environment, install these packages and obtain the matching models once. The following optional setup downloads the selected model packages.

```sh
python -m pip install stanza==1.15.0 torch==2.14.1 numpy==2.5.4
python -c "import stanza; from libri.experiments.stanza_probe.probe import PROCESSORS; stanza.download('en', model_dir='models/stanza', package=None, processors=PROCESSORS)"
```

For a live check on the supplied inputs:

```sh
python -m libri.b_pipeline.run \
  --input fixtures/new12/inputs.json --models models/stanza \
  --save-observed runs/live.new12.parse.json --output runs/live.new12.candidates.json
```

For your own input, supply a JSON array with a unique string `id` and `text` per item. When a paragraph contains several sentences, supply `target_span` for exactly one full parser sentence. See the fixtures for the source metadata and preparation fields used with real documentation. For a quick parse of your own single sentence, the minimal input is:

```json
[{"id":"smoke-001","text":"The service writes logs by default."}]
```

## Examples and evaluation

For an importer, use the [four complete output records](examples/candidates.plain.json). For evaluation, see the [32-target comparison](development/README.md).

## Source versions

The Git and Docker documentation snapshots under `libri/sources/` enable source verification during extraction. The records identify the source path, revision, raw paragraph range and SHA-256. Git documentation comes from [git/git at a0189536](https://github.com/git/git/tree/a018953688f1b10bddf91bff8747068f5f4746a4/Documentation); the snapshots are accompanied by Git's COPYING. Docker documentation comes from [docker/docs at dc98e192](https://github.com/docker/docs/tree/dc98e192e7879ab36c55905844feda9685487324/content/manuals/engine); the snapshots are accompanied by its LICENSE. These four documents were used for development. Use new document groups for independent evaluation.
