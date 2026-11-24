from django.db import migrations

def crear_grupo_glosador(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name='Glosador')

def noop(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('glosa', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(crear_grupo_glosador, noop),
    ]
