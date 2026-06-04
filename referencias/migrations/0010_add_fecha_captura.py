from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('referencias', '0009_cuenta_gastos'),
    ]

    operations = [
        migrations.AddField(
            model_name='referencia',
            name='fecha_captura',
            field=models.DateField(blank=True, null=True),
        ),
    ]
