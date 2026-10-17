# libri

libri keeps restrictions attached to statements extracted from technical documentation. The resulting graph can be saved and searched, with each result linked to its source.

**Status:** Design stage. This README describes the planned implementation.

## Example

> If the queue is full, the scheduler must not assign GPUs to new jobs.

Extracting only "scheduler assigns GPUs to jobs" turns a conditional prohibition into an unconditional claim. The intended structure is:

```text
Conditional
|-- condition: full(queue)
`-- consequence: Obligation
    `-- Not
        `-- assign
            |-- Actor:     scheduler
            |-- Theme:     GPUs
            `-- Recipient: new jobs
```

A search for "GPUs" should return this statement with its condition and prohibition.

## Architecture

The project has three main parts: NLP extraction, an OCaml semantic core, and a standalone information-loss benchmark.

```text
                   Text / Markdown
                          |
                          v
          +--------------------------------+
          | Preparation (OCaml)            |
          | Source archive and span maps   |
          +--------------------------------+
                          |
                          v
          +--------------------------------+
          | 1. NLP extraction (Python)     |
          | Stanza + extraction rules      |
          | Mentions, frames, scope choices|
          +--------------------------------+
                          |
                          v
          +--------------------------------+
          | Frozen extraction bundle       |
          | Candidates and source evidence |
          | Model and rule versions        |
          +--------------------------------+
                          |
                          v
          +--------------------------------+
          | 2. Semantic core (OCaml)        |
          | Validate and build typed IR    |
          | Normalize and select context   |
          +--------------------------------+
                          |
             +------------+-------------+
             |                          |
             v                          v
      JSON export / reload     CLI inspection / graph views
             |                          |
             +------------+-------------+
                          |
                          v
          +--------------------------------+
          | 3. Information-loss benchmark  |<--- LLM / other tool outputs
          | Shared tasks and annotations   |
          | Scoring and error reports      |
          +--------------------------------+
```

**1. NLP extraction (Python)** extracts terms and predicate-argument frames. Its rules propose where negation, modality, and conditions apply. Ambiguous input can produce several interpretations.

**2. Semantic core (OCaml)** owns source preparation and validates the candidates before building the typed intermediate representation. Search and graph views operate on this IR, keeping the required context attached to each selected statement.

**3. Information-loss benchmark (Python)** will compare how much source meaning libri, LLM-based pipelines, and other extraction or retrieval tools preserve. It will provide its own dataset, system adapters, and scoring reports. Systems can participate without adopting libri's IR.

**The extraction bundle is the boundary between the extractor and the semantic core.** It records the candidates with their source spans and model and rule versions. Freezing this output lets us change the core without rerunning NLP, or replay the same input when investigating a conversion error.

## Representation

| Layer | Purpose | Example |
| --- | --- | --- |
| Mention | One phrase occurrence with its modifiers and source spans | "available GPUs" |
| Frame | A predicate with role-labeled arguments | assign(Actor, Theme, Recipient) |
| Scoped claim | Qualifiers around a frame or a group of frames | Obligation(Not(assign(...))) |

Mentions can share a search group while keeping their local modifiers. "Available GPUs" and "failed GPUs" remain distinct occurrences.

Unresolved references stay visible. Alternative interpretations remain available, and unsupported clauses retain their original text.

## First milestone

- Accept English text and Markdown with source mappings.
- Extract active and passive clauses, including properties and multi-argument relations.
- Attach a limited set of negation, modal, and conditional structures.
- Save and reload the IR as JSON.
- Search across documents with the required context included.
- Run libri and a direct LLM baseline through the benchmark on the same tasks.

Cross-sentence coreference and a terminal browser come after this pipeline works.

## Information-loss benchmark

We plan to annotate 120 sentences from Git, Docker, and Linux documentation: 60 for development and 60 held out. The split is by document. Annotations will identify source claims, their arguments, and the restrictions needed to preserve their meaning.

The first comparison will ask each system to extract claims from the same passages. Later tasks will test whether those claims survive storage and retrieval. The benchmark will support libri, direct LLM extraction, and other tools through adapters. Each comparison will use the same source text and task; output budgets will be matched where applicable.

Information loss includes omitted content and damaged meaning. We will report them separately:

| Measure | What it checks |
| --- | --- |
| Semantic retention | Fraction of annotated source claims recovered with the required arguments and restrictions intact |
| Restriction retention | Whether negation, modality, and conditions remain attached to the correct claim |
| Overclaim rate | Fraction of reviewed output claims that drop a restriction or otherwise assert more than the source supports |
| Coverage | How much input a system handles, including unresolved cases and abstentions |

Scoring will use source annotations and human review rather than exact matches to libri's representation. Retention and coverage will accompany overclaim rate, so a system cannot appear reliable just by returning very little. Saved runs will record model and tool versions, prompts, and configuration.

Within libri, we will compare a basic subject-verb-object baseline with frame extraction, then add scope handling. Span and role F1 will help locate extraction errors.

Separate core tests will use hand-authored bundles to check source alignment and scope retention through JSON export and reload.

## Authors

Avery Qiu, Ashley Ou and Benny Chen
