from django.test import TestCase

from .storage_backends import MediaStorage


class MediaStoragePrivacyTest(TestCase):
    def test_default_acl_es_privado(self):
        self.assertEqual(MediaStorage.default_acl, 'private')

    def test_querystring_auth_habilitado(self):
        self.assertTrue(MediaStorage.querystring_auth)

    def test_querystring_expire_una_hora(self):
        self.assertEqual(MediaStorage.querystring_expire, 3600)


from django.db.models.signals import post_delete, pre_save
from django.dispatch.dispatcher import NONE_ID, _make_id

from finanzas.models import XMLProveedor
from .storage_backends import delete_file_on_model_delete, delete_old_file_on_change


class SenalesAcotadasATest(TestCase):
    # Nota: `Signal.receivers` en la versión instalada (Django 6.0.5) guarda
    # tuplas de 4 elementos `(lookup_key, receiver_ref, sender_ref, is_async)`,
    # no `(lookup_key, ref)` de 2 elementos. `lookup_key` sí es
    # `(_make_id(receiver), _make_id(sender))` como se esperaba — confirmado
    # contra `Signal.connect` en
    # .venv/lib/python3.12/site-packages/django/dispatch/dispatcher.py.
    def test_post_delete_conectada_solo_a_xmlproveedor(self):
        receiver_id = _make_id(delete_file_on_model_delete)
        sender_ids = [
            sender_id for (rid, sender_id), _ref, _sender_ref, _is_async in post_delete.receivers
            if rid == receiver_id
        ]
        self.assertEqual(sender_ids, [id(XMLProveedor)])
        self.assertNotIn(NONE_ID, sender_ids)

    def test_pre_save_conectada_solo_a_xmlproveedor(self):
        receiver_id = _make_id(delete_old_file_on_change)
        sender_ids = [
            sender_id for (rid, sender_id), _ref, _sender_ref, _is_async in pre_save.receivers
            if rid == receiver_id
        ]
        self.assertEqual(sender_ids, [id(XMLProveedor)])
        self.assertNotIn(NONE_ID, sender_ids)
