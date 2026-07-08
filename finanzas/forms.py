from decimal import Decimal

from django import forms
from .models import Anticipo, ConceptoFactura, Factura, GastoReferencia, CuentaContable, Pago


class AnticipoForm(forms.ModelForm):
    class Meta:
        model = Anticipo
        fields = ['fecha', 'monto', 'moneda', 'forma_pago', 'num_operacion',
                  'observaciones', 'cuenta_destino']
        widgets = {
            'fecha': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'monto': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'observaciones': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta_destino'].queryset = (
            CuentaContable.objects.filter(numero__startswith='1-100', es_hoja=True)
        )
        self.fields['cuenta_destino'].empty_label = 'Bancos (predeterminado)'
        for field in self.fields.values():
            field.widget.attrs.setdefault('class',
                'w-full border border-slate-200 rounded-lg px-3 py-2 text-sm '
                'text-slate-700 focus:outline-none focus:ring-2 focus:ring-sky-400 '
                'focus:border-transparent'
            )


class GastoReferenciaForm(forms.ModelForm):
    class Meta:
        model = GastoReferencia
        fields = ['tipo', 'concepto', 'fecha', 'monto', 'moneda',
                  'proveedor', 'num_factura_proveedor', 'cuenta_gasto']
        widgets = {
            'fecha': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'monto': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
            'concepto': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta_gasto'].queryset = (
            CuentaContable.objects.filter(tipo='G', es_hoja=True)
        )
        self.fields['cuenta_gasto'].empty_label = 'Gastos varios (predeterminado)'
        for field in self.fields.values():
            field.widget.attrs.setdefault('class',
                'w-full border border-slate-200 rounded-lg px-3 py-2 text-sm '
                'text-slate-700 focus:outline-none focus:ring-2 focus:ring-sky-400 '
                'focus:border-transparent'
            )


_TAILWIND_INPUT = (
    'w-full border border-slate-200 rounded-lg px-3 py-2 text-sm '
    'text-slate-700 focus:outline-none focus:ring-2 focus:ring-sky-400 '
    'focus:border-transparent'
)


class FacturaForm(forms.ModelForm):
    # Campos extra para el concepto (no son parte del modelo Factura)
    concepto_descripcion = forms.CharField(
        max_length=1000,
        label='Descripción del concepto',
        widget=forms.Textarea(attrs={'rows': 2}),
    )
    valor_unitario = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0.01'),
        label='Importe a facturar (sin IVA)',
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
    )

    class Meta:
        model = Factura
        fields = [
            'rfc_receptor', 'nombre_receptor',
            'domicilio_fiscal_receptor', 'regimen_fiscal_receptor',
            'uso_cfdi', 'forma_pago', 'metodo_pago', 'moneda',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', _TAILWIND_INPUT)


class PagoForm(forms.Form):
    """Formulario para registrar un pago contra una Factura TIMBRADA."""

    factura = forms.ModelChoiceField(
        queryset=Factura.objects.filter(estado='TIMBRADA').select_related('configuracion_fiscal'),
        label='Factura a pagar',
        empty_label='Selecciona una factura timbrada...',
    )
    fecha_pago = forms.DateField(
        label='Fecha de pago',
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
    )
    monto = forms.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal('0.01'),
        label='Monto pagado',
        widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
    )
    forma_pago = forms.CharField(
        max_length=2, label='Forma de pago (c_FormaPago)',
        help_text='03=Transferencia, 04=Tarjeta crédito, 01=Efectivo',
    )
    moneda = forms.CharField(max_length=3, initial='MXN', label='Moneda')
    num_operacion = forms.CharField(
        max_length=100, required=False, label='Número de operación bancaria',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', _TAILWIND_INPUT)
