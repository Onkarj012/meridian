# Sleeve F autopsy provenance

The failed Phase-2 attempts are preserved in:

- `runs/optinet-sleeve-f-full-phase2/attempt1-crash/`
- `runs/optinet-sleeve-f-full-phase2/attempt3-crash/`

Both crash directories trace to zero-OI/zero-volume rows in the raw futures CSVs. The representative failure was `ValueError: oi must be > 0`, including `intranet_optinet nifty_fut 2021-05-14` at file line 3.

The ingest handling was fixed in `ingest/optinet_data.py` to handle zero open-interest and zero-volume rows. The affected input contained 48 zero-OI rows and 666 zero-volume rows out of approximately 466,000 total rows.

The crash directories are expected to be deleted after this note lands. They were not deleted as part of this autopsy artifact work.
