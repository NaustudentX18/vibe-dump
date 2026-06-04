# Architecture

Milestone 0/1 establishes a lean fake-first Python package. The intended device
runtime remains one Python process with one web worker, actor-style queues, and
serialized SQLite writes.
