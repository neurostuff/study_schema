"""The extraction pipeline, as objects rather than as a script.

    kinds     what the pipeline is made of -- Paper, TableParse, Cost, RunReport
    stages    the steps, each declaring what it needs and what it leaves behind
    repairs   the deterministic fixes applied to a record, in order, with their reasons
    driver    sequencing, parallelism and accounting

Read `stages.BASELINE` for what the pipeline does, and `repairs.build_sequence()` for
what the builder does to the record afterwards. Both are lists.
"""
