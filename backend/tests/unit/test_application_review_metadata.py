"""ORM metadata parity for application document review migration constraints."""

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.sql.elements import TextClause

from app.models.application_review import ApplicationDocumentReview, ApplicationReviewAcknowledgment

pytestmark = pytest.mark.unit


def test_application_review_orm_metadata_matches_active_and_acknowledgment_invariants() -> None:
    review_table = ApplicationDocumentReview.metadata.tables["application_document_reviews"]
    active_index = next(
        index for index in review_table.indexes if index.name == "uq_application_review_active"
    )

    active_index_table = active_index.table
    assert active_index_table is review_table
    assert active_index_table.name == "application_document_reviews"
    assert active_index.unique is True
    assert [column.name for column in active_index.columns] == ["user_id", "package_id"]
    active_predicate = active_index.dialect_options["postgresql"].get("where")
    assert isinstance(active_predicate, TextClause)
    assert active_predicate.text == "state IN ('queued', 'running')"

    acknowledgment_table = ApplicationReviewAcknowledgment.metadata.tables[
        "application_review_acknowledgments"
    ]
    acknowledgment_constraint = next(
        constraint
        for constraint in acknowledgment_table.constraints
        if constraint.name == "uq_application_review_ack_finding"
    )

    assert acknowledgment_constraint.table.name == "application_review_acknowledgments"
    assert isinstance(acknowledgment_constraint, UniqueConstraint)
    assert [column.name for column in acknowledgment_constraint.columns] == [
        "review_id",
        "finding_id",
    ]
