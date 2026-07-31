# Schemas


# Ideation about LLM extraction workflow

| LLM entity identification | Emit |
|---|---|
| `Group` | `local_id`, `name` |
| `Experiment` | `local_id`, `task_name` |
| `Acquisition` | `local_id`, `name` |
| `Preprocessing` | `local_id`, `name` |
| `StatisticalModel` | `local_id`, `name` |
| `Assessment` | `local_id`, `name` |
| `Predictor` | `local_id`, `name` |
| `Condition` | `local_id`, `name` |
| `Concept` | `local_id`, `name` |


independent parsing of tables.

| LLM table parsing | Emit |
|---|---|
| `Analysis` | the coordinates and name of the analysis |




## Notes

Experiments can have multiple acquisitions, from either
simultaneous recordings (EEG+fMRI) for a particular task,
or from multiple sites/or the scanner changing during data collection. I am not representing those on purpose.
