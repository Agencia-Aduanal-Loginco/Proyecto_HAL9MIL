from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reportes', '0003_destinatario_recibe_wa_ia_cuenta_gastos_and_more'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='historialreporte',
            constraint=models.UniqueConstraint(
                condition=models.Q(exitoso=True),
                fields=['tipo', 'periodo_inicio'],
                name='unique_historial_exitoso',
            ),
        ),
    ]
