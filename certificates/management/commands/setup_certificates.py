"""One-shot setup: build the blank certificate and register it as the default."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from certificates.cleaning import build_blank_template
from certificates.models import CertificateTemplate, get_default_template


class Command(BaseCommand):
    help = (
        "Paint the sample student's details out of the university's card and "
        "register the result as the default certificate template."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--rebuild",
            action="store_true",
            help="Re-clean the source scan even if a blank template already exists.",
        )

    def handle(self, *args, **options):
        source = settings.SOURCE_TEMPLATE
        blank = settings.BLANK_TEMPLATE

        if not source.exists():
            raise CommandError(
                f"The source certificate is missing: {source}\n"
                "Put the university's registration card there and retry."
            )

        if options["rebuild"] or not blank.exists():
            build_blank_template(source, blank)
            self.stdout.write(self.style.SUCCESS(f"Built blank template: {blank}"))
        else:
            self.stdout.write(f"Blank template already present: {blank}")

        template = get_default_template()

        if options["rebuild"]:
            # Re-point the stored default at the freshly cleaned image, in
            # place. Deleting and recreating would orphan the template link on
            # every certificate already generated from it.
            from django.core.files import File

            old_name = template.image.name
            with blank.open("rb") as handle:
                template.image.save(blank.name, File(handle), save=True)
            if old_name and old_name != template.image.name:
                template.image.storage.delete(old_name)
            self.stdout.write(self.style.SUCCESS(f"Template image replaced: {old_name} -> {template.image.name}"))

        self.stdout.write(
            self.style.SUCCESS(f"Default template ready: {template.name} (id={template.pk})")
        )
