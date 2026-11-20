from django.core.management.base import BaseCommand

from assistant.indexer import index_case
from cases.models import Case


class Command(BaseCommand):
    help = "Rebuild the assistant's search index (passages and embeddings) for every case."

    def handle(self, *args, **options):
        total = 0
        for case in Case.objects.all():
            count = index_case(case)
            total += count
            self.stdout.write(f"{case.fir_number}: {count} passages")
        self.stdout.write(self.style.SUCCESS(f"Indexed {total} passages."))
