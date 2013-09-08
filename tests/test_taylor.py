import unittest
from taylor.taylor import Taylor, filter_factory
from swift.common.swob import Request, Response
from Cookie import SimpleCookie

class FakeApp(object):
    def __init__(self, conf):
        self.conf = conf

    def __call__(self):
        pass

class TestRequest(unittest.TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_params_alt(self):
        req = Request.blank('/taylor/v1/AUTH_test/TEST0', 
                            environ={'REQUEST_METHOD': 'POST',
                                     'QUERY_STRING': ''})
        self.assertEqual({}, req.params_alt())
        req = Request.blank('/taylor/v1/AUTH_test/TEST0', 
                            environ={'REQUEST_METHOD': 'POST',
                                     'QUERY_STRING': 'test0=test'})
        self.assertEqual({'test0': 'test'}, req.params_alt())
        req = Request.blank('/taylor/v1/AUTH_test/TEST0', 
                            environ={'REQUEST_METHOD': 'POST',
                                     'QUERY_STRING': 'test0=test&test1=test'})
        self.assertEqual({'test0': 'test', 'test1': 'test'}, req.params_alt())

    def test_cookies(self):
        cok = SimpleCookie()
        cok['token'] = 'XXXXXXXXXX'
        req = Request.blank('/taylor/v1/AUTH_test/TEST0',
                            environ={'REQUEST_METHOD': 'POST',
                                     'HTTP_COOKIE': cok['token'].OutputString()})
        self.assertEqual('XXXXXXXXXX', req.cookies(name='token'))
        self.assertEqual(None, req.cookies(name='no'))
        self.assertTrue(isinstance(req.cookies(), SimpleCookie))


class TestResponse(unittest.TestCase):
    def setUp(self):
        self.resp = Response()

    def tearDown(self):
        pass

    def test_set_cookie(self):
        self.resp.set_cookie('token', 'XXXXXXXX')
        self.assertEqual('token=XXXXXXXX', self.resp.environ['HTTP_SET_COOKIE'])
        self.assertEqual('token=XXXXXXXX', self.resp.headers['set-cookie'])
        self.resp.set_cookie('token', 'XXXXXXXX', path='/', comment='foo',
                             domain='example.tld', max_age=10, secure=True,
                             version='1', httponly=True)
        self.assertEqual('token=XXXXXXXX; Comment=foo; Domain=example.tld; httponly; Max-Age=10; Path=/; secure; Version=1',
                         self.resp.environ['HTTP_SET_COOKIE'])


class TestTaylor(unittest.TestCase):
    def setUp(self):
        self.t = filter_factory({})(FakeApp({}))

    def tearDown(self):
        pass

    def test_get_lang(self):
        req = Request.blank('/taylor/v1/AUTH_test/TEST0', 
                            environ={'REQUEST_METHOD': 'GET'})
        self.assertEqual('en', self.t.get_lang(req))
        req = Request.blank('/taylor/v1/AUTH_test/TEST0', 
                            environ={'REQUEST_METHOD': 'GET'},
                            headers={'accept_language': 'ja'})
        self.assertEqual('ja', self.t.get_lang(req))

    def test_add_prefix(self):
        self.assertEqual('http://localhost:8080/taylor/v1/AUTH_test/TEST0',
                        self.t.add_prefix('http://localhost:8080/v1/AUTH_test/TEST0'))

    def test_del_prefix(self):
        self.assertEqual('http://localhost:8080/v1/AUTH_test/TEST0',
                        self.t.del_prefix('http://localhost:8080/taylor/v1/AUTH_test/TEST0'))

    def test_cont_path(self):
        cont = 'http://localhost:8080/v1/AUTH_test/TEST0'
        self.assertEqual(cont, 
                         self.t.cont_path('http://localhost:8080/v1/AUTH_test/TEST0'))
        self.assertEqual(cont, 
                         self.t.cont_path('http://localhost:8080/v1/AUTH_test/TEST0/test.txt'))
        self.assertEqual(cont + '?delimiter=/&prefix=test/', 
                         self.t.cont_path('http://localhost:8080/v1/AUTH_test/TEST0/test/test.txt'))

    def test_paging_items(self):
        whole_list = [('0', '0'), ('1', '1'), ('2','2'), ('3', '3'), ('4', '4'), \
                      ('5', '5'), ('6', '6'), ('7', '7'), ('8', '8'), ('9', '9'), \
                      ('10', '10'), ('11', '11'), ('12','12'), ('13', '13'), ('14', '14'), \
                      ('15', '15'), ('16', '16'), ('17', '17'), ('18', '18'), ('19', '19')]
        self.assertEqual(('', '', ''),
                         self.t.paging_items('', whole_list, 20))
        self.assertEqual(('', '2', '17'),
                         self.t.paging_items('', whole_list, 3))
        self.assertEqual(('', '5', '17'),
                         self.t.paging_items('2', whole_list, 3))
        self.assertEqual(('2', '8', '17'),
                         self.t.paging_items('5', whole_list, 3))
        self.assertEqual(('5', '11', '17'),
                         self.t.paging_items('8', whole_list, 3))
        self.assertEqual(('14', '17', '17'),
                         self.t.paging_items('17', whole_list, 3))
        self.assertEqual(('2', '8', '17'),
                         self.t.paging_items('4', whole_list, 3))
        self.assertEqual(('5', '11', '17'),
                         self.t.paging_items('6', whole_list, 3))
