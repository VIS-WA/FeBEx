# Slidev Evaluation Deck Workflow

This folder contains the evaluation-only deck in Slidev format.

## Files

- `slides.md`: E1–E8 evaluation slides (including live visualizer placeholder slide).
- `merge_pptx.py`: utility to merge exported eval deck into the main PPTX.

## 1) Run Slidev locally

From workspace root:

```bash
npx @slidev/cli presentations/slidev-evals/slides.md
```

## 2) Export to PPTX

```bash
npx @slidev/cli export --format pptx presentations/slidev-evals/slides.md --output presentations/slidev-evals/evaluations.pptx
```

## 3) Merge with the main presentation

Install merge dependency once:

```bash
source /home/labiour/Documents/venv/bin/activate
pip install pptxcompose
```

Then merge:

```bash
source /home/labiour/Documents/venv/bin/activate
python presentations/slidev-evals/merge_pptx.py \
  --main presentations/Final_Presentation_1.pptx \
  --eval presentations/slidev-evals/evaluations.pptx \
  --out presentations/Final_Presentation_merged.pptx \
  --after-title "Evaluation methodology"
```

Notes:
- `--after-title` inserts eval slides right after the matching main-deck slide title.
- If no match is found, eval slides are appended before the final "Stay Tuned" slide when present.
