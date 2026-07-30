# Data migration: seed AP exam codes and award types

from django.db import migrations


EXAM_CODES = [
    ("07", "United States History"),
    ("10", "African American Studies"),
    ("13", "Art History"),
    ("14", "Drawing"),
    ("15", "2-D Art and Design"),
    ("16", "3-D Art and Design"),
    ("20", "Biology"),
    ("22", "Seminar"),
    ("23", "Research"),
    ("25", "Chemistry"),
    ("28", "Chinese Language and Culture"),
    ("31", "Computer Science A"),
    ("32", "Computer Science Principles"),
    ("33", "Computer Science AB"),
    ("34", "Microeconomics"),
    ("35", "Macroeconomics"),
    ("36", "English Language and Composition"),
    ("37", "English Literature and Composition"),
    ("40", "Environmental Science"),
    ("43", "European History"),
    ("48", "French Language and Culture"),
    ("51", "French Literature"),
    ("53", "Human Geography"),
    ("55", "German Language and Culture"),
    ("57", "United States Government and Politics"),
    ("58", "Comparative Government and Politics"),
    ("60", "Latin"),
    ("61", "Latin Literature"),
    ("62", "Italian Language and Culture"),
    ("64", "Japanese Language and Culture"),
    ("65", "Precalculus"),
    ("66", "Calculus AB"),
    ("68", "Calculus BC"),
    ("69", "Calculus BC: AB Subscore"),
    ("70", "Networking"),
    ("71", "Cybersecurity"),
    ("75", "Music Theory"),
    ("76", "Music Aural Subscore"),
    ("77", "Music Non-Aural Subscore"),
    ("78", "Physics B"),
    ("80", "Physics C: Mechanics"),
    ("82", "Physics C: Electricity and Magnetism"),
    ("83", "Physics 1"),
    ("84", "Physics 2"),
    ("85", "Psychology"),
    ("87", "Spanish Language and Culture"),
    ("89", "Spanish Literature and Culture"),
    ("90", "Statistics"),
    ("93", "World History: Modern"),
]

AWARD_TYPES = [
    ("01", "AP Scholar"),
    ("02", "AP Scholar with Honor"),
    ("03", "AP Scholar with Distinction"),
    ("04", "State AP Scholar"),
    ("05", "National AP Scholar"),
    ("06", "National AP Scholar (Canada)"),
    ("07", "AP International Diploma"),
    ("08", "DoDEA AP Scholar"),
    ("09", "International AP Scholar"),
    ("12", "National AP Scholar (Bermuda)"),
    ("13", "AP Capstone Diploma"),
    ("14", "AP Seminar and Research Certificate"),
]


def seed_ap_codes(apps, schema_editor):
    APExamCode = apps.get_model('dataLoader', 'APExamCode')
    APAwardType = apps.get_model('dataLoader', 'APAwardType')

    for code, name in EXAM_CODES:
        APExamCode.objects.get_or_create(code=code, defaults={'name': name})

    for code, description in AWARD_TYPES:
        APAwardType.objects.get_or_create(code=code, defaults={'description': description})


def reverse_seed(apps, schema_editor):
    APExamCode = apps.get_model('dataLoader', 'APExamCode')
    APAwardType = apps.get_model('dataLoader', 'APAwardType')

    codes = [c for c, _ in EXAM_CODES]
    APExamCode.objects.filter(code__in=codes).delete()

    codes = [c for c, _ in AWARD_TYPES]
    APAwardType.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('dataLoader', '0011_ap_models'),
    ]

    operations = [
        migrations.RunPython(seed_ap_codes, reverse_seed),
    ]
