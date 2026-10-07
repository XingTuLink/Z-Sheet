"""Data understanding: deterministic profiling and later type inference.

Day 6 adds null / duplicate / unique profiling and deterministic sampling
over the ingestion layer's ParsedSheet. Everything here is pure computation
over already-parsed rows — no AI, no I/O, no storage.
"""
