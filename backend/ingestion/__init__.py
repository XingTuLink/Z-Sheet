"""File ingestion: parse uploaded workbooks into raw, structured tables.

Day 5 scope (design 11.1): .xlsx (multi-sheet) and .csv, sheet discovery and
header-row detection. No type inference happens here; every cell is surfaced
as its raw string (or None when empty) so downstream profiling/inference
(Days 6-8) sees one uniform shape regardless of source format.
"""
