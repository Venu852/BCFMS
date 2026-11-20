import copy
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import Department, Role
from cases import integrity, services
from cases.models import Case, Chargesheet, InvestigationNote, Verdict
from ledger import attacks, chain
from ledger.models import Block, Transaction
from ledger.services import sync_role

User = get_user_model()


@override_settings(BCFMS_LLM_ENABLED=False)
class ChainTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        station = Department.objects.create(name="Test Station", code="TST", kind=Department.POLICE)

        def make(username, role):
            user = User.objects.create_user(username, password="x", role=role, department=station, is_approved=True)
            return user

        cls.admin = User.objects.create_superuser("root", password="x")
        cls.police = make("police", Role.POLICE)
        cls.io = make("io", Role.INVESTIGATOR)
        cls.forensic = make("forensic", Role.FORENSIC)
        cls.judge = make("judge", Role.JUDICIARY)
        sync_role(cls.admin)
        for user in (cls.police, cls.io, cls.forensic, cls.judge):
            sync_role(user, by=cls.admin)

    def new_case(self):
        case = Case(
            incident_date=date(2026, 1, 5), incident_location="Main Road", district="Test", crime_type="Theft",
            sections_applied="BNS 303(2)", description="A bicycle was stolen.", complainant_name="A. Complainant",
        )
        services.file_case(self.police, case)
        return case

    def test_full_workflow_keeps_chain_and_records_valid(self):
        case = self.new_case()
        services.change_status(self.police, case, "UNDER_INVESTIGATION")
        services.add_note(self.io, case, InvestigationNote(title="Lead", text="CCTV shows a suspect."))
        services.file_chargesheet(self.io, case, Chargesheet(court="Court 1", sections="BNS 303(2)", summary="Proved."))
        services.record_verdict(self.judge, case, Verdict(outcome="CONVICTED", judgement="Guilty."))
        case.refresh_from_db()
        self.assertEqual(case.status, "CONVICTED")
        self.assertTrue(integrity.verify_case(case)["ok"])
        report = chain.verify_chain(deep=True)
        self.assertTrue(report["valid"], report["invalid_blocks"])

    def test_contract_rejects_wrong_role_and_wrong_order(self):
        case = self.new_case()
        with self.assertRaises(services.ChainRejected):  # verdict before any chargesheet, by a non-judge
            services.record_verdict(self.forensic, case, Verdict(outcome="ACQUITTED", judgement="x"))
        with self.assertRaises(services.ChainRejected):  # cannot skip straight to CLOSED from FILED
            services.change_status(self.police, case, "CLOSED")
        with self.assertRaises(services.ChainRejected):  # notes need an open investigation
            services.add_note(self.io, case, InvestigationNote(title="t", text="t"))
        case.refresh_from_db()
        self.assertEqual(case.status, "FILED")
        self.assertFalse(hasattr(case, "verdict"))
        self.assertEqual(Transaction.objects.filter(status=False).count(), 3)  # attempts stay on-chain
        self.assertTrue(chain.verify_chain(deep=True)["valid"])

    def test_tampering_with_a_block_is_detected(self):
        self.new_case()
        keys, order = chain.authority_keys()
        blocks = [b.to_dict() for b in Block.objects.order_by("number")]
        self.assertTrue(all(r["ok"] for r in chain.validate_blocks(blocks, keys, order)))
        forged = copy.deepcopy(blocks)
        forged[-1]["transactions"][0]["args"]["record_hash"] = "0x" + "ab" * 32
        self.assertFalse(chain.validate_blocks(forged, keys, order)[-1]["ok"])

    def test_database_tampering_is_detected(self):
        case = self.new_case()
        Case.objects.filter(pk=case.pk).update(sections_applied="lesser offence")  # bypasses the application
        case.refresh_from_db()
        self.assertFalse(integrity.verify_case(case)["ok"])

    def test_replay_is_rejected(self):
        self.new_case()
        tx = Transaction.objects.filter(status=True).last().to_dict()
        result = chain.simulate(tx)
        self.assertFalse(result["accepted"])
        self.assertIn("replay", result["error"])

    def test_every_attack_scenario_is_detected_without_touching_the_ledger(self):
        self.new_case()
        height = Block.objects.count()
        for name in attacks.ATTACKS:
            result = attacks.run(name)
            self.assertTrue(result.get("detected"), name)
        self.assertEqual(Block.objects.count(), height)
        self.assertTrue(chain.verify_chain(deep=True)["valid"])

    def test_case_visibility_follows_department_and_sharing(self):
        case = self.new_case()
        lab = Department.objects.create(name="Lab", code="LAB", kind=Department.FORENSIC)
        outsider = User.objects.create_user("lab", password="x", role=Role.FORENSIC, department=lab, is_approved=True)
        sync_role(outsider, by=self.admin)
        self.assertFalse(Case.objects.visible_to(outsider).exists())
        services.share_case(self.police, case, lab, "Analysis requested")
        self.assertTrue(Case.objects.visible_to(outsider).filter(pk=case.pk).exists())
        self.assertFalse(Case.objects.visible_to(self.judge).exists())  # no chargesheet yet
