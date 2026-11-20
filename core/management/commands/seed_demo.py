"""Populate the system with fictional departments, officers and case files.

Everything is created through the same service layer the web UI uses, so every
record is hashed, sent through the smart contracts and sealed into blocks.
All names, addresses and events are invented.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Department, Role
from cases import services
from cases.models import Accused, Case, Chargesheet, Evidence, ForensicReport, Hearing, InvestigationNote, Task, Verdict
from ledger.services import sync_role

User = get_user_model()
PASSWORD = "Bcfms@2026"


def ago(days, hour=10, minute=0):
    return (timezone.now() - timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def ahead(days, hour=11):
    return (timezone.now() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


class Command(BaseCommand):
    help = "Create demo departments, users and case files (all fictional)."

    def handle(self, *args, **options):
        if Case.objects.exists():
            raise CommandError("Cases already exist. Seed only into an empty database.")

        dept = {
            code: Department.objects.get_or_create(code=code, defaults={"name": name, "kind": kind, "district": district})[0]
            for code, name, kind, district in [
                ("HQ", "Police Headquarters", Department.ADMIN, "Bengaluru Urban"),
                ("KOR", "Koramangala Police Station", Department.POLICE, "Bengaluru Urban"),
                ("IND", "Indiranagar Police Station", Department.POLICE, "Bengaluru Urban"),
                ("CYB", "Cyber Crime Police Station", Department.POLICE, "Bengaluru Urban"),
                ("FSL", "State Forensic Science Laboratory", Department.FORENSIC, "Bengaluru Urban"),
                ("CCS", "City Civil and Sessions Court", Department.COURT, "Bengaluru Urban"),
            ]
        }

        def user(username, first, last, role, code, badge):
            u, created = User.objects.get_or_create(
                username=username,
                defaults=dict(first_name=first, last_name=last, role=role, department=dept[code], badge_number=badge,
                              email=f"{username}@bcfms.example", is_approved=True),
            )
            if created:
                u.set_password(PASSWORD)
                if role == Role.ADMIN:
                    u.is_staff = u.is_superuser = True
                u.save()
            return u

        admin = user("admin", "Anita", "Deshmukh", Role.ADMIN, "HQ", "HQ-0001")
        ravi = user("officer.ravi", "Ravi", "Kumar", Role.POLICE, "KOR", "KOR-1123")
        meena = user("officer.meena", "Meena", "Iyer", Role.POLICE, "IND", "IND-2204")
        arjun = user("io.arjun", "Arjun", "Nair", Role.INVESTIGATOR, "KOR", "KOR-1410")
        fatima = user("io.fatima", "Fatima", "Sheikh", Role.INVESTIGATOR, "CYB", "CYB-3307")
        nisha = user("forensic.nisha", "Nisha", "Verma", Role.FORENSIC, "FSL", "FSL-0456")
        judge = user("judge.rao", "Srinivas", "Rao", Role.JUDICIARY, "CCS", "CCS-0072")

        sync_role(admin)
        for u in (ravi, meena, arjun, fatima, nisha, judge):
            sync_role(u, by=admin)
        self.stdout.write("Users created and roles granted on-chain.")

        def fir(officer, days, **fields):
            case = Case(filed_at=ago(days, 9, 30), **fields)
            services.file_case(officer, case)
            return case

        def accused(officer, case, name, age, gender, address, status):
            services.add_accused(officer, case, Accused(name=name, age=age, gender=gender, address=address, status=status))

        def note(io, case, days, title, text):
            services.add_note(io, case, InvestigationNote(title=title, text=text, created_at=ago(days, 16)))

        def evidence(officer, case, days, title, kind, filename, content, description, location):
            item = Evidence(title=title, kind=kind, description=description, collected_at=ago(days, 14), collected_location=location, created_at=ago(days, 15))
            services.register_evidence(officer, case, item, filename, content.encode("utf-8"), "text/plain")
            return item

        # ---- 1. Vehicle theft: full lifecycle, ends in a conviction -------------
        c1 = fir(
            ravi, 96, crime_type="Theft", priority="HIGH", incident_date=ago(97).date(),
            incident_location="5th Block, Koramangala, near Jyoti Nivas College", district="Bengaluru Urban",
            sections_applied="BNS 303(2)", complainant_name="Suresh Menon", complainant_contact="98450 11223",
            complainant_address="No. 14, 5th Block, Koramangala, Bengaluru",
            description="The complainant parked his silver Honda City, registration KA-01-MN-4521, outside his residence at "
            "about 22:30. At 06:15 the next morning the car was missing. The steering lock was found broken on the road. "
            "A neighbour's CCTV camera covers the parking spot.",
        )
        services.change_status(ravi, c1, "UNDER_INVESTIGATION")
        services.assign_investigator(ravi, c1, arjun)
        accused(ravi, c1, "Imran Pasha", 29, "Male", "Shed No. 7, Bommanahalli, Bengaluru", "ARRESTED")
        e1 = evidence(
            ravi, c1, 95, "CCTV log from neighbouring house", "DOCUMENT", "cctv-log-5th-block.txt",
            "CCTV extraction log - camera CAM-02, House No. 16, 5th Block Koramangala\n"
            "01:42:10 Two-wheeler KA-05-HX-9087 (black Pulsar) stops beside silver Honda City KA-01-MN-4521.\n"
            "01:43:55 Rider in a red hooded jacket breaks the driver-side quarter glass.\n"
            "01:47:20 Honda City reverses out and leaves towards Hosur Road. Pillion rider follows on the Pulsar.\n"
            "Extracted by Officer Ravi Kumar in the presence of the house owner, Mrs. Lakshmi Rao.",
            "Timestamped log of the footage showing the theft.", "House No. 16, 5th Block, Koramangala",
        )
        e2 = evidence(
            arjun, c1, 90, "Steering lock recovered at scene", "PHYSICAL", "steering-lock-seizure-memo.txt",
            "Seizure memo: one broken yellow steering lock, brand Autocop, recovered from the road surface at the parking spot. "
            "Sealed in evidence bag KOR/EV/2211 and sent to the State Forensic Science Laboratory for fingerprint examination.",
            "Broken steering lock, sealed and sent for fingerprint analysis.", "5th Block, Koramangala",
        )
        note(arjun, c1, 93, "Two-wheeler traced", "The Pulsar KA-05-HX-9087 seen in the CCTV footage is registered to Imran Pasha of "
             "Bommanahalli. He has two earlier vehicle theft cases at Madiwala police station.")
        services.transfer_custody(arjun, e2, nisha, "Sent to FSL for latent fingerprint development")
        services.add_forensic_report(nisha, e2, ForensicReport(
            title="Latent fingerprint examination of steering lock", created_at=ago(84, 12),
            methodology="Cyanoacrylate fuming followed by comparison against the AFIS database.",
            findings="Three latent prints were developed on the lock shackle. Two are of comparison quality. Both match the "
            "right thumb and right index finger of Imran Pasha (AFIS ID KA-2021-118834) with 14 and 16 matching minutiae.",
            conclusion="The steering lock was handled by Imran Pasha.",
        ))
        note(arjun, c1, 82, "Arrest and recovery", "Imran Pasha was arrested at his shed in Bommanahalli. The Honda City KA-01-MN-4521 "
             "was recovered from a garage in Hosur, Tamil Nadu, with a fake number plate TN-70-AB-1190. The garage owner, "
             "Selvam, identified Pasha as the person who delivered the car.")
        services.file_chargesheet(arjun, c1, Chargesheet(
            court="City Civil and Sessions Court, Hall 12", sections="BNS 303(2), 317(2)", filed_at=ago(70, 11),
            summary="CCTV footage, fingerprint evidence on the steering lock and recovery of the vehicle establish that the "
            "accused Imran Pasha stole the Honda City KA-01-MN-4521 and sold it to a garage in Hosur.",
            witnesses="Suresh Menon (complainant)\nLakshmi Rao (CCTV owner)\nSelvam (garage owner)\nNisha Verma (forensic expert)",
        ))
        services.schedule_hearing(judge, c1, Hearing(scheduled_for=ago(40, 11), court="City Civil and Sessions Court, Hall 12", purpose="Framing of charges"))
        services.record_verdict(judge, c1, Verdict(
            outcome="CONVICTED", delivered_at=ago(12, 15), sentence="Three years rigorous imprisonment and a fine of Rs. 25,000",
            judgement="The prosecution proved beyond reasonable doubt that the accused committed theft of the vehicle. The "
            "fingerprint report and the recovery at the instance of the accused are conclusive.",
        ))

        # ---- 2. House burglary: under investigation, shared with the lab --------
        c2 = fir(
            ravi, 34, crime_type="Burglary", priority="HIGH", incident_date=ago(35).date(),
            incident_location="No. 221, 6th Block, Koramangala", district="Bengaluru Urban",
            sections_applied="BNS 305, 331(4)", complainant_name="Dr. Kavitha Reddy", complainant_contact="99000 45678",
            complainant_address="No. 221, 6th Block, Koramangala, Bengaluru",
            description="The complainant returned from a three-day trip to find the rear kitchen door forced open. Gold "
            "jewellery weighing about 180 grams, a Rolex wristwatch and Rs. 2,40,000 in cash were missing from the bedroom "
            "almirah. The CCTV recorder (DVR) had been removed by the intruders.",
        )
        services.change_status(ravi, c2, "UNDER_INVESTIGATION")
        services.assign_investigator(ravi, c2, arjun)
        e3 = evidence(
            arjun, c2, 33, "Inventory of stolen property", "DOCUMENT", "stolen-property-inventory.txt",
            "Stolen property list - FIR Koramangala burglary\n1. Gold necklace, 62 g, temple design\n2. Six gold bangles, 78 g\n"
            "3. Two gold chains, 40 g\n4. Rolex Datejust wristwatch, serial 7T2K9981\n5. Cash Rs. 2,40,000 in Rs. 500 notes\n"
            "Total estimated value Rs. 21,50,000.",
            "List prepared with the complainant.", "No. 221, 6th Block, Koramangala",
        )
        e4 = evidence(
            arjun, c2, 33, "Tool-mark cast from kitchen door", "PHYSICAL", "toolmark-cast-memo.txt",
            "Silicone cast of pry marks on the rear kitchen door frame, 18 mm wide flat blade. Sealed as KOR/EV/2290.",
            "Cast of the pry marks left on the door frame.", "No. 221, 6th Block, Koramangala",
        )
        note(arjun, c2, 31, "Scene examination", "Entry was through the rear kitchen door, forced with a flat tool about 18 mm wide. "
             "The DVR was taken, which suggests the offenders knew the house. The domestic help, Manjula, stopped coming to work "
             "four days before the burglary and her phone is switched off.")
        note(arjun, c2, 24, "Pawn shop lead", "A pawn broker in Chickpet, Jain Jewellers, reported a man trying to sell six gold "
             "bangles matching the description. He gave the name Ramesh and a mobile number ending 4417. The same number was in "
             "contact with Manjula's phone eleven times in the week before the burglary.")
        services.share_case(arjun, c2, dept["FSL"], "Tool-mark comparison and fingerprint examination requested")
        services.transfer_custody(arjun, e4, nisha, "Sent to FSL for tool-mark comparison")
        services.add_forensic_report(nisha, e4, ForensicReport(
            title="Tool-mark comparison", created_at=ago(18, 13),
            methodology="Comparison microscopy of the cast against test marks.",
            findings="The marks were made by a flat pry bar with an 18 mm blade and a distinctive notch 4 mm from the left edge. "
            "The same notch pattern appears in the tool-mark casts from an unsolved burglary at HSR Layout in March.",
            conclusion="The same tool was probably used in both burglaries.",
        ))
        services.add_task(ravi, c2, Task(title="Obtain call detail records for the number ending 4417", assigned_to=arjun, due_date=ahead(3).date()))

        # ---- 3. UPI phishing fraud: chargesheet filed, hearing upcoming ---------
        c3 = fir(
            fatima, 58, crime_type="Cybercrime", priority="MEDIUM", incident_date=ago(60).date(),
            incident_location="Online (victim residing in Indiranagar)", district="Bengaluru Urban",
            sections_applied="IT Act 66C, 66D; BNS 318(4)", complainant_name="Prakash Shetty", complainant_contact="97410 33221",
            complainant_address="Flat 302, Defence Colony, Indiranagar, Bengaluru",
            description="The complainant received an SMS saying his electricity connection would be cut and called the number "
            "given. The caller made him install a screen-sharing app and Rs. 4,85,000 was debited from his account in five UPI "
            "transactions within twenty minutes.",
        )
        services.change_status(fatima, c3, "UNDER_INVESTIGATION")
        accused(fatima, c3, "Deepak Yadav", 24, "Male", "Jamtara, Jharkhand", "ARRESTED")
        evidence(
            fatima, c3, 56, "Bank statement of beneficiary account", "DOCUMENT", "beneficiary-account-statement.txt",
            "Statement - Canara Bank account 5123 0098 7766, holder Deepak Yadav, Jamtara branch\n"
            "Credits on the incident date: Rs. 99,000; Rs. 99,000; Rs. 99,000; Rs. 99,000; Rs. 89,000 by UPI from Prakash Shetty.\n"
            "Rs. 4,70,000 withdrawn by ATM at Jamtara within three hours. The account also received Rs. 1,20,000 from "
            "an unrelated victim, Sunitha Rao, on the following day.",
            "Statement obtained from the bank under notice.", "Canara Bank, nodal office",
        )
        note(fatima, c3, 52, "Money trail", "All five UPI transfers went to Canara Bank account 5123 0098 7766 held by Deepak Yadav. "
             "The calling number was a SIM issued on forged documents in West Bengal. The screen-sharing app used was AnyDesk.")
        services.file_chargesheet(fatima, c3, Chargesheet(
            court="City Civil and Sessions Court, Hall 4", sections="IT Act 66C, 66D; BNS 318(4), 319(2)", filed_at=ago(20, 12),
            summary="The accused Deepak Yadav impersonated an electricity board official, gained remote access to the "
            "complainant's phone and transferred Rs. 4,85,000 to his own account, which he withdrew the same day.",
            witnesses="Prakash Shetty (complainant)\nNodal officer, Canara Bank\nFatima Sheikh (Investigating Officer)",
        ))
        services.schedule_hearing(judge, c3, Hearing(scheduled_for=ahead(9), court="City Civil and Sessions Court, Hall 4", purpose="Framing of charges"))
        services.schedule_hearing(judge, c3, Hearing(scheduled_for=ahead(30), court="City Civil and Sessions Court, Hall 4", purpose="Examination of the complainant"))

        # ---- 4. Online job fraud: linked by the same mule account ---------------
        c4 = fir(
            fatima, 21, crime_type="Fraud", priority="MEDIUM", incident_date=ago(23).date(),
            incident_location="Online (victim residing in Koramangala)", district="Bengaluru Urban",
            sections_applied="IT Act 66D; BNS 318(4)", complainant_name="Sunitha Rao", complainant_contact="96320 77881",
            complainant_address="PG Hostel, 1st Block, Koramangala, Bengaluru",
            description="The complainant was offered a part-time job rating hotels on Telegram. After small initial payouts she was "
            "asked to pay 'task deposits' and transferred Rs. 1,20,000 before the group stopped responding.",
        )
        services.change_status(fatima, c4, "UNDER_INVESTIGATION")
        note(fatima, c4, 17, "Beneficiary account matches earlier case", "The deposit of Rs. 1,20,000 was sent to Canara Bank account "
             "5123 0098 7766, the same mule account used in the UPI phishing case against Deepak Yadav. This indicates one group "
             "is running both the electricity-bill scam and the Telegram job scam.")

        # ---- 5. Assault: freshly filed ------------------------------------------
        c5 = fir(
            meena, 2, crime_type="Assault", priority="MEDIUM", incident_date=ago(3).date(),
            incident_location="Outside Toit Brewpub, 100 Feet Road, Indiranagar", district="Bengaluru Urban",
            sections_applied="BNS 115(2), 351(2)", complainant_name="Rohit Fernandes", complainant_contact="98860 55412",
            complainant_address="12th Main, HAL 2nd Stage, Indiranagar, Bengaluru",
            description="The complainant was assaulted by three unknown men after an argument over parking at about 23:45. "
            "He sustained a fracture of the left forearm and was treated at Chinmaya Mission Hospital. One assailant wore a "
            "delivery company jacket and left on a white Activa.",
        )

        # ---- 6. Chain snatching: under investigation ----------------------------
        c6 = fir(
            meena, 15, crime_type="Robbery", priority="HIGH", incident_date=ago(15).date(),
            incident_location="CMH Road, near Indiranagar Metro Station", district="Bengaluru Urban",
            sections_applied="BNS 304(2)", complainant_name="Padma Krishnan", complainant_contact="94480 21099",
            complainant_address="Binnamangala, Indiranagar, Bengaluru",
            description="Two men on a black Pulsar snatched the complainant's 40 gram gold chain while she was walking home at "
            "about 19:10. The pillion rider wore a red hooded jacket. The motorcycle had no rear number plate.",
        )
        services.change_status(meena, c6, "UNDER_INVESTIGATION")
        services.assign_investigator(meena, c6, arjun)
        note(arjun, c6, 11, "Similar method to earlier theft", "The description - black Pulsar and a rider in a red hooded jacket - "
             "resembles the pair seen in the Koramangala Honda City theft. Imran Pasha was in judicial custody on this date, so "
             "his associate on the Pulsar, not yet identified, is the likely suspect.")
        services.add_task(meena, c6, Task(title="Collect metro station CCTV for 19:00-19:30", assigned_to=arjun, due_date=ahead(1).date()))

        # ---- 7. Missing person: closed after being traced -----------------------
        c7 = fir(
            ravi, 47, crime_type="Missing Person", priority="CRITICAL", incident_date=ago(48).date(),
            incident_location="St. Francis School, 3rd Block, Koramangala", district="Bengaluru Urban",
            sections_applied="BNS 137(2)", complainant_name="Joseph D'Souza", complainant_contact="99801 66754",
            complainant_address="3rd Block, Koramangala, Bengaluru",
            description="The complainant's 15-year-old son did not return from school. He was last seen at the school gate at 15:40 "
            "carrying a blue backpack.",
        )
        services.change_status(ravi, c7, "UNDER_INVESTIGATION")
        services.assign_investigator(ravi, c7, arjun)
        note(arjun, c7, 46, "Boy traced", "The boy was traced at Mysuru railway station by the Railway Protection Force. He had left "
             "home after an argument about exam results. He was restored to his parents before the Child Welfare Committee. "
             "No offence is disclosed.")
        services.change_status(arjun, c7, "CLOSED")

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {Case.objects.count()} cases. Sign in with any of: "
            + ", ".join(u.username for u in (admin, ravi, meena, arjun, fatima, nisha, judge))
            + f" — password {PASSWORD}"
        ))
