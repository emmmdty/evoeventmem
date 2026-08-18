"""Mechanism evaluation: gold pairs, offline replay, and Eval A metrics.

This package implements the Eval A half of the O09 mechanism evaluation
(see ``docs/superpowers/specs/2026-08-17-o09-mechanism-eval-and-500-consistency-design.md``).
Everything here is a read-only, deterministic recomputation over sealed run
artifacts; it never invokes a reader or extractor and never mutates ``runs/``
artifacts produced by other stages.
"""
