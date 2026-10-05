# Validation

The scripts were compared with the originals on 5 October 2026 using Python
3.12.14, pypdf 6.10.0, and Poppler.

For the saved input of 5,550 records, both matching scripts retained 3,940
records and excluded 1,610. Record content and order, individual decisions,
matching evidence, and counts agreed. Fourteen additional records were used
to check formatting and matching boundaries.

RIS parsing and existing metadata agreed for all 664 records in the local
input. Four synthetic screening results were compared for annotation,
splitting, merging, and category export. A two-page PDF, including a blank page,
produced the same five text chunks and two rendered page images.

These comparisons were completed before the output files and screening notes
were shortened.

The original scripts were not modified.
