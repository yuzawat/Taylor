# -*- coding: utf-8; mode: python -*-
import cgi
from Cookie import SimpleCookie, CookieError, Morsel
from mako.lookup import TemplateLookup
from mako import exceptions
from os.path import join, abspath, dirname, basename
from mimetypes import guess_type
from swiftclient import Connection, ClientException, \
    get_auth, get_account, get_container, get_object, \
    put_container, put_object, delete_container, delete_object, \
    head_container, head_object, post_container, post_object
from swift.common.http import *
from swift.common.swob import Request, Response, wsgify, \
    HTTPNotFound, HTTPFound
from swift.common.utils import split_path
from time import time
from types import MethodType
from urlparse import urlsplit, urlunsplit, parse_qsl, urlparse
from urllib import quote, unquote

"""
Swift Built-in Object Manipulator

----------------
Setting

[pipeline:main]
pipeline = healthcheck taylor cache tempauth proxy-server

[filter:taylor]
use = egg:handle#handle
page_path = /taylor
auth_url = http://localhost:8080/auth/v1.0
----------------
"""


# thease methods are added to swob.Request.
def params_alt(self):
    """ add http parameter routine """
    if self._params_cache is None:
        f_para = {}
        q_para = {}
        fs = cgi.FieldStorage(
            environ=self.environ,
            fp=self.environ['wsgi.input'])
        for i in fs.keys():
            if fs[i].filename:
                item = (fs[i].filename, fs[i].file)
            else:
                item = fs.getfirst(i)
            f_para[i] = item
        if 'QUERY_STRING' in self.environ:
            q_para = dict(parse_qsl(self.environ['QUERY_STRING'], True))
        f_para.update(q_para)
        if len(f_para):
            self._params_cache = f_para
        else:
            self._params_cache = {}
    return self._params_cache


def cookies(self, name=None):
    """ add method to get Cookie """
    cookies = self.environ.get('HTTP_COOKIE', '')
    cok = SimpleCookie()
    try:
        cok.load(cookies)
    except CookieError, msg:
        return cok
    if name:
        if cok.get(name, ''):
            return cok[name].value
        return None
    return cok

Request.params_alt = MethodType(params_alt, None, Request)
Request.cookies = MethodType(cookies, None, Request)


# this method is added to swob.Response.
def set_cookie(self, name, value, expires=None, path=None,
               comment=None, domain=None, max_age=None,
               secure=None, version=None, httponly=None):
    """ add method to set Cookie, but only one cookie """
    cok = SimpleCookie()
    cok[name] = value
    if expires:
        cok[name]['expires'] = expires
    if path:
        cok[name]['path'] = path
    if comment:
        cok[name]['comment'] = comment
    if domain:
        cok[name]['domain'] = domain
    if max_age:
        cok[name]['max-age'] = max_age
    if secure:
        cok[name]['secure'] = secure
    if version:
        cok[name]['version'] = version
    if httponly:
        cok[name]['httponly'] = httponly
    self.environ['HTTP_SET_COOKIE'] = cok[name].OutputString()
    self.headers['Set-Cookie'] = cok[name].OutputString()


Response.set_cookie = MethodType(set_cookie, None, Response)


def copy_object(url, token, from_cont, from_obj, to_cont, to_obj=None,
                http_conn=None, proxy=None):
    """ add to swiftclient """
    to_obj_name = to_obj if to_obj else from_obj
    return put_object(url, token, to_cont, name=to_obj_name, contents=None,
                      content_length=0,
                      headers={'X-Copy-From': '/%s/%s' %
                               (from_cont, from_obj)},
                      http_conn=http_conn, proxy=proxy)


class Taylor(object):
    """ swift embeded easy manipulator """
    def __init__(self, app, conf):
        """
        """
        self.app = app
        self.conf = conf
        self.page_path = conf.get('page_path', '/taylor')
        self.auth_url = conf.get('auth_url')
        self.items_per_page = int(conf.get('items_per_page', 20))
        self.cookie_max_age = int(conf.get('cookie_max_age', 36000))
        self.path = abspath(dirname(__file__))
        self.title = conf.get('taylor_title', 'Taylor')
        self.tmpl = TaylorTemplate()
        self.token_bank = {}

    @wsgify
    def __call__(self, req):
        login_path = '%s/%s' % (self.page_path, 'login')
        token = None
        storage_url = None

        # favicon
        if req.path == '/favicon.ico':
            return self.pass_file(req, 'images/favicon.ico',
                                  'image/vnd.microsoft.icon')

        # not taylor
        if not req.path.startswith(self.page_path):
            return self.app

        # image
        if req.path.startswith(join(self.page_path, 'image')):
            return self.pass_file(req,
                                  join('images', basename(req.path)))
        # css
        if req.path.startswith(join(self.page_path, 'css')):
            return self.pass_file(req,
                                  join('css', basename(req.path)))
        # js
        if req.path.startswith(join(self.page_path, 'js')):
            return self.pass_file(req,
                                  join('js', basename(req.path)))

        # get token from cookie
        token = req.cookies('_token')
        status = self.token_bank.get(token, None)
        if status:
            storage_url = status.get('url', None)

        # login page
        if req.path == login_path:
            return self.login(req)
        if not token or not storage_url:
            return HTTPFound(location=login_path)
        self.token_bank[token].update({'last': time()})

        # after action
        if '_action' in req.params_alt():
            if req.params_alt()['_action'] == 'logout':
                del self.token_bank[token]
                return HTTPFound(location=login_path)
            return self.page_after_action(req, storage_url, token)

        # construct main pages
        return self.page(req, storage_url, token)

    def pass_file(self, req, path, content_type=None):
        """ pass a file to client """
        resp = Response()
        if content_type:
            resp.content_type = content_type
        else:
            (ctype, enc) = guess_type(basename(path))
            resp.content_type = ctype
        resp.charset = None
        try:
            with open(join(self.path, path)) as f:
                resp.app_iter = iter(f.read())
                return resp
        except IOError:
            return HTTPNotFound(request=req)

    def login(self, req):
        """ create login page """
        if req.method == 'POST':
            try:
                username = req.params_alt().get('username')
                password = req.params_alt().get('password')
                (storage_url, token) = get_auth(self.auth_url,
                                                username, password)
                if self.token_bank.get(token, None):
                    self.token_bank[token].update({'url': storage_url,
                                                   'last': int(time())})
                else:
                    self.token_bank[token] = {'url': storage_url,
                                              'last': int(time())}
                resp = HTTPFound(location=self.add_prefix(storage_url))
                resp.set_cookie('_token', token, path=self.page_path,
                                max_age=self.cookie_max_age)
                return resp
            except KeyError, err:
                print 'Key Error: %s' % err
            except ClientException, err:
                lang = req.headers.get('Accept-Language', 'en,').split(',')[0]
                resp = Response(charset='utf8')
                resp.app_iter = self.tmpl({'ptype': 'login',
                                           'top': self.page_path,
                                           'title': self.title, 'lang': lang,
                                           'message': 'Login Failed'})
                return resp
            except Exception, err:
                print 'Error: %s' % err
        token = req.cookies('_token')
        status = self.token_bank.get(token, None) if token else None
        lang = req.headers.get('Accept-Language', 'en,').split(',')[0]
        msg = ''
        if status:
            msg = status.get('msg', '')
        resp = Response(charset='utf8')
        resp.app_iter = self.tmpl({'ptype': 'login',
                                   'top': self.page_path,
                                   'title': self.title, 'lang': lang,
                                   'message': msg})
        if msg:
            self.token_bank[token].update({'msg': ''})
        return resp

    def page_after_action(self, req, storage_url, token):
        """ page after action """
        path = urlparse(self.del_prefix(req.url)).path
        vrs, acc, cont, obj = split_path(path, 1, 4, True)
        path_type = len([i for i in [vrs, acc, cont, obj] if i])
        params = req.params_alt()
        action = params.get('_action')
        if action == 'cont_create' or action == 'obj_create':
            rc = self.action_routine(req, storage_url, token)
            if rc == HTTP_CREATED:
                self.token_bank[token].update({'msg': 'Create Success'})
            elif rc == HTTP_BAD_REQUEST:
                self.token_bank[token].update({'msg': ''})
            else:
                self.token_bank[token].update({'msg': 'Create Failed'})
            if action == 'cont_create':
                loc = storage_url
            else:
                loc = self.cont_path(path)
        if action == 'cont_delete' or action == 'obj_delete':
            if self.action_routine(req, storage_url, token) == HTTP_NO_CONTENT:
                self.token_bank[token].update({'msg': 'Delete Success'})
            else:
                self.token_bank[token].update({'msg': 'Delete Failed'})
            if action == 'cont_delete':
                loc = storage_url
            else:
                loc = self.cont_path(path)
        if action == 'obj_copy':
            if self.action_routine(req, storage_url, token) == HTTP_CREATED:
                self.token_bank[token].update({'msg': 'Copy Success'})
            else:
                self.token_bank[token].update({'msg': 'Copy Failed'})
            loc = self.cont_path(path)
        if action == 'cont_metadata' or action == 'obj_metadata' or \
           action == 'cont_acl':
            if self.action_routine(req, storage_url, token) == HTTP_ACCEPTED:
                if action == 'cont_acl':
                    self.token_bank[token].update(
                        {'msg': 'ACL update Success'})
                else:
                    self.token_bank[token].update(
                        {'msg': 'Metadata update Success'})
            else:
                if action == 'cont_acl':
                    self.token_bank[token].update({'msg': 'ACL update Failed'})
                else:
                    self.token_bank[token].update(
                        {'msg': 'Metadata update Failed'})
            if action == 'cont_metadata' or action == 'cont_acl':
                loc = storage_url
            else:
                loc = self.cont_path(path)
        resp = HTTPFound(location=self.add_prefix(loc))
        resp.set_cookie('_token', token, path=self.page_path,
                        max_age=self.cookie_max_age)
        return resp

    def page(self, req, storage_url, token):
        """ main page container list or object list """
        path = urlparse(self.del_prefix(req.url)).path
        if len(path.split('/')) <= 2:
            path = urlparse(storage_url).path
        vrs, acc, cont, obj = split_path(path, 1, 4, True)
        lang = req.headers.get('Accept-Language', 'en,').split(',')[0]
        path_type = len([i for i in [vrs, acc, cont, obj] if i])
        base = self.add_prefix(urlparse(storage_url).path)
        status = self.token_bank.get(token, None)
        msg = ''
        delete_confirm = req.params_alt().get('delete_confirm', '')
        acl_edit = req.params_alt().get('acl_edit', '')
        meta_edit = req.params_alt().get('meta_edit', '')

        if status:
            msg = status.get('msg', '')
        if path_type == 2:  # account
            try:
                (acct_status, cont_list) = get_account(storage_url, token)
            except ClientException, err:
                pass
            cont_meta = {}
            cont_acl = {}
            edit_param = [acl_edit, delete_confirm, meta_edit]
            if any(edit_param):
                edit_cont = filter(None, edit_param)[0]
                cont_list = [cont for cont in cont_list
                             if cont['name'] == edit_cont]
            for i in cont_list:
                meta = head_container(storage_url, token, i['name'])
                cont_meta[i['name']] = dict(
                    [(m[len('x-container-meta-'):].capitalize(), meta[m])
                     for m in meta.keys()
                     if m.startswith('x-container-meta')])
                cont_acl[i['name']] = dict(
                    [(m[len('x-container-'):], meta[m])
                     for m in meta.keys()
                     if m.startswith('x-container-read')
                     or m.startswith('x-container-write')])
            resp = Response(charset='utf8')
            resp.set_cookie('_token', token, path=self.page_path,
                            max_age=self.cookie_max_age)
            resp.app_iter = self.tmpl({'ptype': 'containers',
                                       'title': self.title,
                                       'lang': lang,
                                       'top': self.page_path,
                                       'account': acc,
                                       'message': msg,
                                       'base': base,
                                       'containers': cont_list,
                                       'container_meta': cont_meta,
                                       'container_acl': cont_acl,
                                       'delete_confirm': delete_confirm,
                                       'acl_edit': acl_edit,
                                       'meta_edit': meta_edit})
            self.token_bank[token].update({'msg': ''})
            return resp
        if path_type == 3:  # container
            try:
                (acct_status, cont_list) = get_account(storage_url, token)
                (cont_status, obj_list) = get_container(storage_url, token,
                                                        cont)
            except ClientException, err:
                pass
            obj_meta = {}
            cont_names = [i['name'] for i in cont_list]
            edit_param = [acl_edit, delete_confirm, meta_edit]
            if any(edit_param):
                edit_obj = filter(None, edit_param)[0]
                obj_list = [obj for obj in obj_list if obj['name'] == edit_obj]
            for i in obj_list:
                meta = head_object(storage_url, token, cont, i['name'])
                obj_meta[i['name']] = dict(
                    [(m[len('x-object-meta-'):].capitalize(), meta[m])
                     for m in meta.keys() if m.startswith('x-object-meta')])
            resp = Response(charset='utf8')
            resp.set_cookie('_token', token, path=self.page_path,
                            max_age=self.cookie_max_age)
            base = '/'.join(base.split('/') + [cont])
            resp.app_iter = self.tmpl({'ptype': 'objects',
                                       'title': self.title,
                                       'lang': lang,
                                       'top': self.page_path,
                                       'account': acc,
                                       'container': cont,
                                       'message': msg,
                                       'base': base,
                                       'objects': obj_list,
                                       'object_meta': obj_meta,
                                       'cont_names': cont_names,
                                       'delete_confirm': delete_confirm,
                                       'acl_edit': acl_edit,
                                       'meta_edit': meta_edit})
            self.token_bank[token].update({'msg': ''})
            return resp
        if path_type == 4:  # object
            try:
                (obj_status, objct) = get_object(storage_url, token, cont, obj)
            except ClientException, e:
                resp.status = e.http_status
                return resp
            except err:
                pass
            resp = Response()
            resp.set_cookie('_token', token, path=self.page_path,
                            max_age=self.cookie_max_age)
            resp.status = HTTP_OK
            resp.headers = obj_status
            resp.body = objct
            self.token_bank[token].update({'msg': ''})
            return resp
        return HTTPFound(location=self.add_prefix(storage_url))

    def add_prefix(self, url):
        """ add path prefix (like '/taylor') to URL """
        p = urlsplit(url)
        path = self.page_path + p.path
        return urlunsplit((p.scheme, p.netloc, path, p.query, p.fragment))

    def del_prefix(self, url):
        """ delete path prefix (like '/taylor') to URL """
        p = urlsplit(url)
        path = '/' + '/'.join(p.path.split('/')[2:])
        return urlunsplit((p.scheme, p.netloc, path, p.query, p.fragment))

    def cont_path(self, url):
        """ return Swift Container URL """
        p = urlsplit(url)
        vrs, acc, cont, obj = split_path(p.path, 1, 4, True)
        if obj:
            path = '/'.join(p.path.split('/')[:-1])
        else:
            path = '/'.join(p.path.split('/'))
        return urlunsplit((p.scheme, p.netloc, path, p.query, p.fragment))

    def metadata_check(self, form):
        """ """
        removing = [i[len('remove-'):] for i in form.keys()
                    if i.startswith('remove-')]
        headers = {}
        for h in [i for i in form.keys()
                  if i.startswith('x-container-meta-') or
                  i.startswith('x-object-meta-')]:
            headers.update({h: form[h]})
        for k in headers.keys():
            if k in removing:
                headers[k] = ''
        for i in range(10):
            if 'container_meta_key%s' % i in form:
                keyname = 'x-container-meta-' + \
                form['container_meta_key%s' % i].lower()
                headers.update({keyname:
                                form['container_meta_val%s' % i]})
                continue
            if 'object_meta_key%s' % i in form:
                keyname = 'x-object-meta-' + \
                form['object_meta_key%s' % i].lower()
                headers.update({keyname:
                                form['object_meta_val%s' % i]})
        return headers

    def acl_check(self, form):
        """ """
        removing = [i[len('remove-'):]
                    for i in form.keys() if i.startswith('remove-')]
        headers = {}
        for acl in ['x-container-read', 'x-container-write']:
            headers.update({acl: form.get(acl, 'blank')})
            if acl in removing:
                headers.update({acl: ''})
            if headers[acl] == 'blank':
                del(headers[acl])
        return headers

    def action_routine(self, req, storage_url, token):
        """ execute action """
        path = urlparse(self.del_prefix(req.url)).path
        vrs, acc, cont, obj = split_path(path, 1, 4, True)
        path_type = len([i for i in [vrs, acc, cont, obj] if i])
        params = req.params_alt()
        action = params.get('_action')
        lines = int(params.get('_line', self.items_per_page))
        page = int(params.get('_page', 0))
        marker = str(params.get('_marker', ''))
        cont = params.get('cont_name', cont)
        obj_param = params.get('obj_name')
        if obj_param and len(obj_param) == 2:
            obj_name, obj_fp = obj_param
        else:
            obj_name, obj_fp = ('', None)
        from_cont = params.get('from_container', None)
        from_obj = params.get('from_object', None)
        to_cont = params.get('to_container', None)
        to_obj = params.get('to_object', None)
        headers = self.metadata_check(params)
        headers.update(self.acl_check(params))

        if action == 'cont_list':
            (acct_status, cont_list) = get_account(storage_url,
                                                   token,
                                                   marker=marker,
                                                   limit=lines)
            #resp.app_iter = [json.dumps(c) for c in cont_list]
        if action == 'cont_create':
            if cont:
                try:
                    put_container(storage_url, token, cont)
                except ClientException, err:
                    return err.http_status
                return HTTP_CREATED
            return HTTP_BAD_REQUEST
        if action == 'obj_list':
            (cont_status, obj_list) = get_container(storage_url,
                                                    token, cont,
                                                    marker=marker,
                                                    limit=lines)
            #resp.app_iter = [json.dumps(o) for o in obj_list]
        if action == 'cont_delete':
            try:
                delete_container(storage_url, token, cont)
            except ClientException, err:
                return err.http_status
            return HTTP_NO_CONTENT
        if action == 'cont_metadata' or action == 'cont_acl':
            try:
                post_container(storage_url, token, cont, headers)
            except ClientException, err:
                return err.http_status
            return HTTP_ACCEPTED
        if action == 'obj_create':
            if obj_name:
                try:
                    put_object(storage_url, token, cont, obj_name, obj_fp)
                except ClientException, err:
                    return err.http_status
                return HTTP_CREATED
            return HTTP_BAD_REQUEST
        if action == 'obj_get':
            (obj_status, hunk) = get_object(storage_url, token, cont, obj)
            #resp.headerlist = obj_status.items()
            #resp.body_file = hunk
        if action == 'obj_delete':
            try:
                delete_object(storage_url, token, cont, obj)
            except ClientException, err:
                return err.http_status
            return HTTP_NO_CONTENT
        if action == 'obj_metadata':
            try:
                post_object(storage_url, token, cont, obj, headers)
            except ClientException, err:
                return err.http_status
            return HTTP_ACCEPTED
        if action == 'obj_copy':
            try:
                copy_object(storage_url, token, cont, from_obj,
                            to_cont, to_obj)
            except ClientException, err:
                return err.http_status
            return HTTP_CREATED


def filter_factory(global_conf, **local_conf):
    """Returns a WSGI filter app for use with paste.deploy."""
    conf = global_conf.copy()
    conf.update(local_conf)

    def taylor_filter(app):
        return Taylor(app, conf)
    return taylor_filter


class TaylorTemplate(object):
    """ HTML Template """
    def __init__(self):
        tmpldir = join(abspath(dirname(__file__)), 'templates')
        self.tmpls = TemplateLookup(directories=tmpldir,
                                    output_encoding='utf-8',
                                    encoding_errors='replace')

    def __call__(self, values):
        try:
            tmpl = self.tmpls.get_template('taylor.tmpl')
            return tmpl.render(**values)
        except:
            return exceptions.html_error_template().render()
