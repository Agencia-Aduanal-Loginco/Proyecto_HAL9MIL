from django.test import TestCase

from .storage_backends import MediaStorage


class MediaStoragePrivacyTest(TestCase):
    def test_default_acl_es_privado(self):
        self.assertEqual(MediaStorage.default_acl, 'private')

    def test_querystring_auth_habilitado(self):
        self.assertTrue(MediaStorage.querystring_auth)

    def test_querystring_expire_una_hora(self):
        self.assertEqual(MediaStorage.querystring_expire, 3600)
