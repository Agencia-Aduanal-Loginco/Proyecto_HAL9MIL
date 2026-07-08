from django.db import migrations


def crear_grupo_finanzas(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name='Finanzas')


def eliminar_grupo_finanzas(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name='Finanzas').delete()


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(crear_grupo_finanzas, eliminar_grupo_finanzas),
    ]
