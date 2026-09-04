1. Imports no longer wipe the current configuration
- The command is now additive by default: it merges into existing assessments/supports instead of deleting them.
- Wiping is now opt-in via --clear (the old --no-clear flag is gone).
2. Imports are tagged
- Added tag to Assessment and Accommodation (migration 0016); column_letter is no longer globally unique so different source files may reuse letters.
- The tag is displayed:
- in the Configured Assessments list (dark chip next to the name),
- on every card in the Master Designated Supports Pool (dark chip next to the code),
- in the Django admin (columns + filters).
- Supports key off (tag, column_letter), so importing CAASPP and ELPAC keeps each file's supports as separate, tagged records while shared assessment names are deduplicated.
3. Tag passed as a variable
docker compose exec django python manage.py import_desi_config --csv ref/ELPAC_Upload_Stu.csv --tag ELPAC
docker compose exec django python manage.py import_desi_config --csv ref/CAASPP_Upload.csv --tag CAASPP --clear
--tag is optional — if omitted it defaults to the CSV filename stem. Still combineable with --active and --dry-run.
