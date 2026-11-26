# jobs/: batch/offline units
| Dir | Module | Runs as | Phase |
|---|---|---|---|
| ingestion | M6i Ingestion (cards, dedupe, chunk, embed, index, snapshot/restore) | CronJob + Job | P3 |
| finetune | M10t Fine-tuning (dataset, review CLI, train notebook/script, export, compare) | offline (Colab/Kaggle) + model-import Job | P8 |
| evaluation | M13 evaluation harness (labelled set → confusion matrix, behaviour checks) | K8s Job + Jenkins nightly | P9 |
