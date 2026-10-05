# BC-FL literature screening tools

This folder contains two tools: one checks search terms in RIS records, and the
other records screening decisions and prepares PDF text and page images.

## Requirements

Python 3.10 or later is required. RIS processing uses the Python standard library.
PDF text extraction and rendering require Poppler (`pdftotext` and `pdftoppm`).
Install `pypdf` from `requirements-optional.txt` to enable an additional PDF
page-count check.

## Files

- `screen_ris.py`: command-line interface for checking search terms.
- `query_screening.py`: search-term matching and output files.
- `fulltext_tools.py`: command-line interface for screening records and PDFs.
- `ris_results.py`: RIS records and screening notes.
- `pdf_tools.py`: PDF text extraction and page rendering.

The module functions can also be called directly from Python.

## Checking search terms

The tool checks the title (`TI`), abstract (`AB`), and keywords (`KW`) for both
groups of search terms:

~~~text
("federated learning" OR "federated machine learning" OR
 "federated edge learning" OR "federated optimization")
AND (blockchain* OR "block chain*" OR "distributed ledger*")
~~~

The two groups may match different fields. Each phrase must occur within a
single field value. Before matching, the text is normalized for Unicode, HTML
entities, case, soft hyphens, dashes, and whitespace. A trailing wildcard matches
word suffixes. Abbreviations such as FL and DLT are not expanded.

Records are excluded only when their `DB` or `DP` field is exactly `IEEE Xplore`
and one or both search-term groups are missing. Records from other sources are
retained.

Run these commands from this folder to preview the counts or save the results:

~~~sh
python screen_ris.py examples/query_example.ris
python screen_ris.py examples/query_example.ris --output-dir results
~~~

With `--output-dir`, the tool writes:

- `retained.ris` and `excluded.ris`: the retained and excluded records.
- `decisions.csv`: the decision and matching fields for each record.
- `summary.txt`: record counts.

The CSV uses UTF-8 with a byte-order mark so it can be opened in spreadsheet
software. Existing output files are not overwritten. Use `--apply` to replace the
input RIS file with the retained records; this also saves `input_backup.ris`.
Run `python screen_ris.py --help` for all options.

## Recording decisions and preparing PDFs

Screening decisions are supplied in a JSON file. The tool adds them as RIS `N1`
notes, merges annotated records, or exports records by status. Notes include the
decision, reason, inclusion criteria, reading source and version, evidence, and
any supplied comments or limitations.

To add a note to the example record:

~~~sh
python fulltext_tools.py record examples/record_example.ris --result examples/pending_result.json --output annotated.ris
~~~

To extract PDF text or render selected pages:

~~~sh
python fulltext_tools.py prepare paper.pdf --work-dir text
python fulltext_tools.py render paper.pdf --pages 2,4-5 --work-dir images
~~~

The commands are `split`, `record`, `merge`, `export`, `prepare`, `render`, and
`doctor`. Run `python fulltext_tools.py <command> --help` for usage. They can be
used independently.

The tool checks the decision codes and required evidence fields. Reviewers make
the eligibility decisions. A PDF `PASS` means that the page-count checks agree.

The files in `examples/` use synthetic records. Previous comparisons with the
original scripts are described in `VALIDATION.md`.
