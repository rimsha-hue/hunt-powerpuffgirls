"""Plain-language labels for the audit trail shown on the Activity screen."""

# Which human gate an action records (spec section 2), for the timeline's gate markers.
GATES: dict[str, int] = {
    "gate_choose": 1,
    "claim_confirmed": 2,
    "claim_edited": 2,
    "claim_removed": 2,
    "claim_marked_false": 2,
    "gate_verify_passed": 2,
    "gate_approve": 3,
    "answer_approved": 3,
    "application_sent": 3,
    "gate_submit": 4,
}

LABELS: dict[str, str] = {
    "signed_up": "You created your account",
    "logged_in": "You signed in",
    "profile_updated": "You updated your profile",
    "resume_ingested": "Your resume was read into the ledger",
    "github_ingested": "Your GitHub repos were read into the ledger",
    "portfolio_ingested": "Your portfolio was read into the ledger",
    "evidence_deleted": "You deleted a fact from the ledger",
    "scout_run": "The Scout searched for jobs",
    "digest_sent": "A digest of new jobs was emailed to you",
    "apply_link_used": "You opened a job from an Apply link",
    "job_added_by_link": "You added a job by its link",
    "job_status": "A job's status changed",
    "gate_choose": "You chose to apply",
    "cv_tailored": "The Tailor rewrote your CV and the Verifier checked it",
    "claim_confirmed": "You confirmed a claim with evidence",
    "claim_edited": "You reworded a claim",
    "claim_removed": "You removed a claim",
    "claim_marked_false": "You marked a claim false",
    "gate_verify_passed": "You approved your CV with every claim proven",
    "gate_approve": "You approved the application email",
    "email_drafted": "The Composer drafted your email",
    "application_sent": "Your application email was sent",
    "form_discovered": "The form's questions were found",
    "form_answered": "Answers to the form were drafted",
    "answer_approved": "You approved a form answer",
    "gate_submit": "You submitted the form on the employer's site",
    "plan_created": "The Coach made a growth plan",
    "plan_check_failed": "A linked repo didn't pass every check yet",
    "plan_verified": "A linked repo passed every check",
    "skill_proven_by_repo": "A skill joined your ledger from GitHub",
    "plan_deleted": "You deleted a growth plan",
}


def label(action: str) -> str:
    return LABELS.get(action) or action.replace("_", " ").capitalize()
