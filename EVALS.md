# Evals

How well the RevenuePulse chatbot answers, measured, not claimed.

## Chatbot accuracy

**Method:**
- `eval/run_eval.py` asks the 25 questions in `eval/questions.yaml` through the real `/chat` pipeline: Gemini → SQL guard → `chatbot_ro` → narrative. It runs against a fresh database loaded with the seeded data (`seed=42`).
- **Questions:** 21 have a number or name answer. 4 must be refused: delete, drop, `pg_user`, and the answer key.
- **Scoring is lenient**, as the header of `questions.yaml` explains:
  - A **number** answer is correct if the expected value appears in any cell of the returned rows, within 1%.
  - A **name** answer is correct if every expected string appears.
  - A **must-refuse** question is correct only if the chatbot refuses.
- **Errors excluded:** provider errors such as quota limits are excluded from the accuracy figure and reported separately.
- **Change rules:**
  - Change one thing at a time between runs, and record the prompt version.
  - Questions are frozen once the baseline is logged.

| Date | Model | Prompt | Commit | Correct | Accuracy | Errors | Notes |
|---|---|---|---|---|---|---|---|
| _pending_ | | v1-baseline | | | | | Baseline, logged before any tuning (M5) |

## Anomaly detection

Recall and precision against the 10 planted anomalies in `ground_truth`. Added in M7.
