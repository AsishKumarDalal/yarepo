# Two Projects: Text-to-SQL Dashboard and a Doc-Reading Coding Model

Two related projects, planned over 1-2 months. Both are about making a small, fine-tuned model useful on real data and real documentation, and both are evaluated against clear baselines so the results can be verified.

**Main research deliverable:** a small coding model that works like a human developer, looking things up in documentation, breaking the problem down, reading, and then solving, instead of relying on memory. The target is to beat a model 3-4x its size on knowledge-heavy coding tasks, at lower latency and cost.

**Status:** planned. Numbers in this document are targets or placeholders (TBD), not results.

---

## Project 1: Text-to-SQL Dashboard with a Fine-tuned Model

### What it is
A web app where a user connects a database, asks questions in plain English, and gets back:
- the generated SQL (visible and editable),
- the query result as a table,
- a chart or dashboard panel they can pin and reuse.

Queries are executed through an agent harness: the model proposes SQL, the harness runs it in a read-only sandbox, feeds back errors or empty results, and lets the model retry.

### Why it is interesting
- Fine-tuning a small model (1-8B) for one SQL dialect can rival much larger general models on that dialect.
- Execution feedback (did the query run, did it return sensible results) is a free, verifiable signal for training and evaluation.
- Data stays local: no database contents leave the user's machine.

### Components
| Part | Description |
|---|---|
| Dashboard UI | Question box, SQL viewer/editor, result table, chart, pinned panels |
| Harness | Schema loader, prompt builder, read-only SQL executor, error-feedback retry loop |
| Model | Base open model, then fine-tuned (LoRA) for the target dialect |
| Eval suite | Execution accuracy on public benchmarks plus a custom schema set |

### Training plan
1. **Baseline:** run the base model (and optionally a larger model) through the harness with no fine-tuning.
2. **Data:** public text-to-SQL training data, plus synthetic question/SQL pairs generated over varied schemas and kept only when the SQL executes and the result passes a check.
3. **SFT:** LoRA fine-tune on verified pairs, including examples of fixing a failed query from its error message.
4. **Optional RL:** GRPO-style training with execution correctness as the reward.
5. **Ablate:** with and without schema linking, with and without the retry loop, with and without fine-tuning.

### Evaluation
- **Metric:** execution accuracy (does the generated query return the same result as the reference query).
- **Benchmarks:** Spider and BIRD for comparability; a small custom set on real-looking business schemas for the demo.
- **Report next to accuracy:** latency, tokens per question, and retry rate.
- **Required comparisons:** base model, base model plus harness, fine-tuned model plus harness.

### Deliverables
- Working dashboard with a demo database.
- Fine-tuned model weights and training scripts.
- Results table and a short write-up of the ablations.

---

## Project 2: Research, a Small Model That Reads Docs Like a Human

### Core idea
When a developer is asked to write code against a library they don't remember, they don't try to recall everything. They open the documentation, find the relevant pages, break the problem into steps, read what each step needs, and then write the code. This project trains a small model to do the same, and to do it fast.

### Hypothesis
> A small coding model (7-10B) trained to **look up documentation, decompose the problem, read, and then solve** can outperform a model 3-4x larger on **knowledge-bottlenecked coding tasks**, while using less latency and cost per solved problem.

### Scope: where this should and should not work
- **Expected to help:** tasks where knowledge is the bottleneck, such as library and API usage, new or changed library versions, and unfamiliar frameworks.
- **Not expected to help much:** hard algorithmic problems where the bottleneck is finding the idea (LiveCodeBench-style). These are used only as a sanity check, and a flat result there is an acceptable, honestly reported outcome.

### Claims, in tiers
| Tier | Claim | Difficulty |
|---|---|---|
| 1 | Small model **with docs** beats the same small model **without docs** | Expected |
| 2 | Trained small model **with docs** beats a 3-4x larger model from the same family **without docs** | Main target |
| 3 | Trained small model with docs **matches the larger model given the same docs** | Stretch goal |

### Research questions
1. Does training the model to navigate documentation (search, open, scan, take notes) beat simply stuffing top-k retrieved chunks into the prompt?
2. Does explicit task decomposition before reading improve accuracy?
3. Can the model learn **when** to read and when to answer directly, so it doesn't waste tokens on easy problems?
4. What accuracy per unit of latency and cost does the system reach compared with larger models?

### System design
```
task
  -> plan (decompose into sub-tasks)
  -> for each sub-task: search docs -> open page -> extract notes
  -> write code using the notes
  -> run tests / fix errors
  -> final answer
```

Interaction format used in training and inference:
```
<plan> sub-task 1, 2, 3 </plan>
<think> reasoning about sub-task 1 </think>
<search> query </search>
<doc> ...retrieved text (inserted by the harness)... </doc>
<notes> what matters from the doc </notes>
<answer> final code </answer>
```
Generation pauses at `</search>`, the harness fetches the result, appends it, and generation continues.

### Training plan
1. **Documentation corpus:** official docs for a set of popular libraries, versioned. Include library versions released after the base model's training data where possible, so memory cannot substitute for reading.
2. **Task data:** synthetic coding tasks over those libraries with unit tests. Keep only tasks whose reference solution passes the tests.
3. **Cold-start SFT:** a few thousand verified traces in the format above (plan, search, notes, code), filtered by passing tests. This teaches the format before RL.
4. **RL (GRPO with LoRA):** multi-turn rollouts with the harness in the loop.
   - Main reward: unit tests pass.
   - Small format reward: valid structure with a plan.
   - Small penalty per doc read and per token, so the model reads only when it needs to.
   - **Mask retrieved `<doc>` tokens out of the loss.** The model is trained on what it generated, not on text the retriever supplied.
5. **Curriculum:** start with single-library tasks and short docs, then move to multi-library tasks and longer navigation.

### Evaluation
**Benchmarks**
- BigCodeBench and DS-1000 (library-heavy), plus a custom set of tasks on newer library versions.
- LiveCodeBench as a secondary check where no gain is expected.
- Comparison models: a model from the same family 3-4x larger (choose from the live leaderboards before committing to the claim).

**Metrics**
| Metric | What it measures |
|---|---|
| pass@1 | Primary accuracy metric, same prompts and sampling settings for every system |
| Wall-clock latency per problem | The "very fast" claim |
| Tokens per solved problem | Cost, including doc tokens read |
| Docs read per problem | Whether the model reads only when needed |
| Accuracy vs. cost plot | A small model on the better frontier is the headline result |

**Required conditions to compare**
1. Small model, no docs.
2. Small model, docs in prompt only (no training).
3. Small model, docs plus trained navigation and decomposition (ours).
4. Larger model, no docs.
5. Larger model, same docs (for the tier 3 claim).

**Ablations**
- Decomposition on vs. off.
- Navigation (search, open, notes) vs. top-k chunks in the prompt.
- Reading penalty on vs. off.
- Cold-start SFT only vs. SFT plus RL.

### Optional extension: non-technical questions
Test whether the same system handles vague, jargon-free requests from non-technical users (for example, by adding a query-rewriting step), and report the accuracy gap between technical and non-technical phrasing. This is secondary to the main deliverable.

### Integrity rules
- Keep benchmark problems and near-duplicates out of all training data, and check.
- Separate documentation versions used for training from those used for evaluation.
- Use the same prompts, sampling settings, and test harness for every system compared.
- Publish negative results, including the LiveCodeBench sanity check.

### Deliverables (main deliverable first)
1. Trained small coding model that reads docs, with weights and training scripts.
2. A comparison against a 3-4x larger model, with the accuracy-versus-cost plot.
3. The documentation-navigation harness and evaluation scripts, reproducible end to end.
4. A write-up answering the research questions, with ablation tables.

---

## Shared Infrastructure
- One harness pattern and one evaluation framework used by both projects.
- Experiment tracking: fixed seeds, saved configs, and a results log for every run.
- Compute: small-GPU friendly (LoRA, quantized inference); rent an A100 only for final runs.

## Suggested Timeline (8 weeks, compressible to ~5)
| Weeks | Project 1 | Project 2 |
|---|---|---|
| 1-2 | Harness, executor, baseline numbers, first dashboard UI | Build doc index, harness, and baseline numbers (conditions 1, 2, 4) |
| 3-4 | Synthetic data, SFT, retry-loop ablation | Task generation with tests, cold-start SFT traces |
| 5-6 | Dashboard polish, optional RL, final benchmark runs | GRPO training with doc-read penalty, first full benchmark runs |
| 7-8 | Write-up, demo, release | Ablations, cost-latency plot, write-up |

To compress to about 5 weeks: skip RL in Project 1, use one benchmark per project, and limit Project 2 to a smaller set of libraries.

## Risks and Mitigations
- **Docs may not help on hard problems:** scope the claim to knowledge-bottlenecked tasks and report the sanity check honestly.
- **Larger model with the same docs also improves:** treat tier 2 as the headline and tier 3 as a stretch goal.
- **Copying retrieved solutions instead of understanding:** include training tasks where the docs only partly match the solution.
- **Leakage:** separate train and eval sources and documentation versions from the start.
- **Reward hacking in RL:** inspect samples regularly; keep rewards simple and verifiable.
- **Scope creep:** finish Project 1's harness and baseline before starting Project 2 training.

## Repository Layout (proposed)
```
/sql-dashboard      app, harness, training, eval
/doc-reader         doc index, navigation harness, training, eval
/shared             evaluation utilities, configs, experiment logs
/docs               write-ups and results
```

## Results
Results will be added here as experiments finish.

| Experiment | Baseline | Ours | Notes |
|---|---|---|---|
| SQL execution accuracy (Spider) | TBD | TBD | |
| SQL execution accuracy (BIRD) | TBD | TBD | |
| BigCodeBench pass@1 (small, no docs vs. ours) | TBD | TBD | Tier 1 |
| BigCodeBench pass@1 (3-4x larger, no docs vs. ours) | TBD | TBD | Tier 2 |
| DS-1000 pass@1 | TBD | TBD | |
| Custom new-library tasks pass@1 | TBD | TBD | |
| LiveCodeBench pass@1 (sanity check) | TBD | TBD | No gain expected |
| Latency and tokens per solved problem | TBD | TBD | |
