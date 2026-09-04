"""Idempotent configuration seeds. Run: python -m app.core.db.seeds.seed_all"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db.sync_ingestion import get_sync_ingestion_session_factory
from app.core.db.models.configuration.case_subtypes import CaseSubtype
from app.core.db.models.configuration.case_types import CaseType
from app.core.db.models.configuration.counties import County
from app.core.db.models.configuration.jurisdictions import Jurisdiction
from app.core.db.models.configuration.questions import Question
from app.core.db.models.configuration.states import State
from app.core.db.models.configuration.workflow_definitions import WorkflowDefinition
from app.core.db.models.configuration.workflow_questions import WorkflowQuestion


def _seed_states(session: Session) -> None:
    state = session.execute(select(State).where(State.code == "TX")).scalar_one_or_none()
    if state is None:
        state = State(code="TX", name="Texas", is_active=True)
        session.add(state)
        session.flush()
    county = session.execute(
        select(County).where(County.state_id == state.id, County.name == "Travis")
    ).scalar_one_or_none()
    if county is None:
        session.add(County(state_id=state.id, code="TRAVIS", name="Travis", is_active=True))


def _seed_case_types(session: Session) -> None:
    ct = session.execute(select(CaseType).where(CaseType.code == "FAMILY")).scalar_one_or_none()
    if ct is None:
        ct = CaseType(code="FAMILY", name="Family Law", is_active=True)
        session.add(ct)
        session.flush()
    st = session.execute(select(CaseSubtype).where(CaseSubtype.code == "DIVORCE_UNCONTESTED")).scalar_one_or_none()
    if st is None:
        session.add(
            CaseSubtype(
                case_type_id=ct.id,
                code="DIVORCE_UNCONTESTED",
                name="Uncontested Divorce",
                is_active=True,
            )
        )


def _seed_questions(session: Session) -> None:
    for code, text, qtype in (
        ("PETITIONER_FULL_NAME", "What is your full legal name?", "TEXT"),
        ("HAS_CHILDREN", "Do you have children together?", "BOOLEAN"),
    ):
        if session.execute(select(Question).where(Question.code == code)).scalar_one_or_none() is None:
            session.add(Question(code=code, question_text=text, question_type=qtype, is_active=True))


def _seed_workflows(session: Session) -> None:
    state = session.execute(select(State).where(State.code == "TX")).scalar_one()
    county = session.execute(
        select(County).where(County.state_id == state.id, County.name == "Travis")
    ).scalar_one()
    jur = session.execute(select(Jurisdiction).where(Jurisdiction.code == "TX-TRAVIS-DISTRICT")).scalar_one_or_none()
    if jur is None:
        jur = Jurisdiction(
            county_id=county.id,
            jurisdiction_type="DISTRICT_COURT",
            code="TX-TRAVIS-DISTRICT",
            name="Travis County District Court",
            is_active=True,
        )
        session.add(jur)
        session.flush()
    subtype = session.execute(select(CaseSubtype).where(CaseSubtype.code == "DIVORCE_UNCONTESTED")).scalar_one()
    wf = session.execute(
        select(WorkflowDefinition).where(
            WorkflowDefinition.jurisdiction_id == jur.id,
            WorkflowDefinition.case_subtype_id == subtype.id,
            WorkflowDefinition.status == "ACTIVE",
        )
    ).scalar_one_or_none()
    if wf is None:
        wf = WorkflowDefinition(
            jurisdiction_id=jur.id,
            case_subtype_id=subtype.id,
            workflow_name="Uncontested Divorce Filing",
            version=1,
            status="ACTIVE",
        )
        session.add(wf)
        session.flush()
    for sort_order, qcode in enumerate(("PETITIONER_FULL_NAME", "HAS_CHILDREN"), start=1):
        q = session.execute(select(Question).where(Question.code == qcode)).scalar_one()
        exists = session.execute(
            select(WorkflowQuestion).where(
                WorkflowQuestion.workflow_id == wf.id, WorkflowQuestion.question_id == q.id
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                WorkflowQuestion(
                    workflow_id=wf.id, question_id=q.id, sort_order=sort_order, is_required=True
                )
            )


def main() -> None:
    SessionLocal = get_sync_ingestion_session_factory()
    with SessionLocal() as session:
        _seed_states(session)
        _seed_case_types(session)
        _seed_questions(session)
        _seed_workflows(session)
        session.commit()
    print("Seeds applied (idempotent).")


if __name__ == "__main__":
    main()
