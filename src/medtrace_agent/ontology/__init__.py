"""Clinical ontology registration for Zep graph projection."""

from medtrace_agent.ontology.clinical import (
    ONTOLOGY_EDGE_TYPES,
    ONTOLOGY_NODE_LABELS,
    apply_clinical_ontology,
    auto_apply_clinical_ontology,
    clinical_ontology_auto_apply_enabled,
)

__all__ = [
    "ONTOLOGY_EDGE_TYPES",
    "ONTOLOGY_NODE_LABELS",
    "apply_clinical_ontology",
    "auto_apply_clinical_ontology",
    "clinical_ontology_auto_apply_enabled",
]
