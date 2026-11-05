# Student list extraction benchmark

Regression dataset for the AI extraction behind **Registered Students > Import from Image/PDF**.
It runs the production code path (`student_import.run_extraction`), so a prompt, model or
preprocessing change is scored exactly as users would experience it.

```bash
# score the model currently configured (ITO AI Settings > Student List LLM Override, else the agent's LLM)
bench --site <site> execute ito_customization.ito_customization.extraction_benchmark.evaluate.run
# score one model without changing any setting, or several side by side
bench --site <site> execute ito_customization.ito_customization.extraction_benchmark.evaluate.run --kwargs "{'llm': 'openrouter/qwen/qwen3.8-flash'}"
bench --site <site> execute ito_customization.ito_customization.extraction_benchmark.evaluate.compare --kwargs "{'llms': ['openai/gpt-5.6-terra', 'openrouter/qwen/qwen3.8-flash']}"
```

Each run prints rows returned vs expected, names matched, subjects, classes, tokens, estimated cost
(from litellm's price list; blank when a model is not priced there) and wall time. Full results,
including every returned row, are written to `results/<model>.json`. Nothing is written to Registered Students.

## Adding a document

1. Copy the file to `files/`.
2. Add a case to `cases.json` (`expected_total`, and any of: `students` with `name`/`class`/`subjects`,
   `expected_counts_by_class`, `rule_every_student_subjects`). Mark names whose handwriting is ambiguous
   `"approx": true`.
3. Run the benchmark and re-check **every** case, not only the new one.

## Dataset

| Case | Layout | What it tests |
|---|---|---|
| `typed_spreadsheet_two_groups` | Excel screenshot, 101 rows in two stacked tables, 13 subject columns in two groups (exam / workbooks) | Dense small print, ignoring the workbook group, subjects missing from the master (ABACUS, ASTRONOMY, CIO), repeated headers |
| `handwritten_photo_wrapped_names` | Photographed ITO sheet, tilted | Names wrapping to a second line, parent name beside the student name, tick-to-column drift, handwritten TOTAL row |
| `handwritten_scan_3pages_rotated` | 3-page scanned PDF, no text layer | First-name-only entries, class written in the margin, a block with no serial numbers, a page scanned sideways |

Ground truth was transcribed by hand. For the typed spreadsheet only 7 names are spot-checked (plus the
row count per class and the subjects of every row); names on the sideways PDF page are lenient (`approx`).
These numbers are a regression signal, not a proof of accuracy on unseen sheets.

## Results (model `openai/gpt-5.6-terra`, one run each)

| Case | | Before (agent v13) | After (v14 + pipeline changes) |
|---|---|---|---|
| typed spreadsheet | rows | 100 / 101 | 101 / 101 |
| | subjects (IMO+EIO on every row) | 100 / 100 returned | 101 / 101 |
| | spot-checked names exact | 3 / 7 | 5 / 7 |
| | cost | $0.103 | $0.096 |
| handwritten photo | rows / names | 19 / 19, 18 exact | 19 / 19, 19 exact |
| | subjects | not re-scored (see note) | 19 / 19 |
| | cost | $0.048 | $0.038 |
| scanned PDF (3 pages) | rows | 59 / 59 | 59 / 59 |
| | names matched | 39 / 39 (38 exact) | 38 / 39 (36 exact) |
| | subjects | 38 / 39 | 37 / 38 |
| | input tokens | 178,104 | 38,300 |
| | cost | $0.438 | $0.128 |

Note: the "before" subject score for the photo was computed against a ground truth that was wrong
(it said ISO where the sheet's TOTAL row proves IMO); it was corrected afterwards and the old agent was not run again.

Runs are not deterministic and each figure is a single run. Between v14 iterations the same sideways
PDF page went from correct names to invented ones ("Ishu", "Shivam", ...) when rotation was left to
the extraction call, which is why orientation is now a separate step.

## Known remaining misses

* `AMAN KAUSHIK` (scanned PDF page 2) gets an extra ISO from the grid reader in every run. The row is flagged
  "Subject scan and AI reading differ" in the review dialog.
* `DHYEYANSH PARMAR` (typed spreadsheet, last row) is misread from the small print.
* Handwritten spellings on the sideways page can differ by a letter; they appear as normal editable rows.
