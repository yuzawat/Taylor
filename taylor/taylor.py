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
from swift.common.middleware.acl import referrer_allowed
from swift.common.swob import Request, Response, wsgify, \
    HTTPNotFound, HTTPFound
from swift.common.utils import split_path, config_true_value, \
    get_logger, cache_from_env
from time import time, mktime, strptime
from types import MethodType
from urlparse import urlsplit, urlunsplit, parse_qsl, urlparse
from urllib import quote, unquote
try:
    import simplejson as json
except ImportError:
    import json

"""
Swift Built-in Object Manipulator

----------------
Setting

[pipeline:main]
pipeline = catch_errors proxy-logging healthcheck cache taylor tempauth proxy-logging proxy-server

[filter:taylor]
use = egg:Taylor#taylor
page_path = /taylor
auth_url = http://localhost:8080/auth/v1.0
auth_version = 1
items_per_page = 5
cookie_max_age = 3600
enable_versions = no
enable_object_expire = no
enable_container_sync = no
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
                      # if URL encorded strings was set in X-Copy-From, we have to 
                      # execute URL encording twice.
                      # '%E6%97%A5%E6%9C%AC%E8%AA%9E'
                      #     -> %25E6%2597%25A5%25E6%259C%25AC%25E8%25AA%259E
                      # See: line 810 in swift-1.8.0/swift/proxy/controllers/obj.py
                      headers={'X-Copy-From': '/%s/%s' %
                               (quote(from_cont), quote(from_obj))},
                      http_conn=http_conn, proxy=proxy)


def icon_image(content_type):
    """ 
    set icon image for content-type
    This is called in objects.tmpl
    """
    if content_type.startswith('image/'):
        return 'image.png'
    if content_type.startswith('audio/'):
        return 'audio.png'
    if content_type.startswith('video/'):
        return 'video.png'
    if content_type.startswith('application/vnd.ms-') or \
       content_type.startswith('application/ms') or \
       content_type.startswith('application/vnd.openxmlformats-officedocument.'):
        return 'office.png'
    if content_type.startswith('application/octet-stream'):
        return 'octed-stream.png'
    if content_type.startswith('application/zip') or \
       content_type.startswith('application/x-apple-diskimage') or \
       content_type.startswith('application/x-tar'):
        return 'archive.png'
    if content_type.startswith('application/x-ruby'):
        return 'ruby.png'
    if content_type.startswith('application/pdf'):
        return 'pdf.png'
    if content_type.startswith('application/'):
        return 'application.png'
    if content_type.startswith('text/x-csrc'):
        return 'c.png'
    if content_type.startswith('text/x-python'):
        return 'python.png'
    if content_type.startswith('text/x-perl'):
        return 'perl.png'
    if content_type.startswith('text/x-ruby'):
        return 'ruby.png'
    if content_type.startswith('text/x-sh'):
        return 'shell.png'
    if content_type.startswith('text/'):
        return 'text.png'
    return 'file.png'


class Taylor(object):
    """ swift embeded easy manipulator """
    def __init__(self, app, conf):
        """
        """
        self.app = app
        self.conf = conf
        self.title = conf.get('taylor_title', 'Taylor')
        self.logger = get_logger(conf, log_route='%s' % self.title)
        self.page_path = conf.get('page_path', '/taylor')
        self.auth_url = conf.get('auth_url')
        self.auth_version = int(conf.get('auth_version', 1))
        self.items_per_page = int(conf.get('items_per_page', 5))
        self.cookie_max_age = int(conf.get('cookie_max_age', 3600))
        self.enable_versions = config_true_value(conf.get('enable_versions', 'no'))
        self.enable_object_expire = config_true_value(conf.get('enable_object_expire', 'no'))
        self.enable_container_sync = config_true_value(conf.get('enable_container_sync', 'no'))
        self.delimiter = conf.get('delimiter', '/')
        self.path = abspath(dirname(__file__))
        self.tmpl = TaylorTemplate()
        self.token_bank = {}
        self.memcache = None
        self.secure = True if 'key_file' in self.conf and 'cert_file' in self.conf else False
        self.logger.info('%s loaded.' % self.title)

    @wsgify
    def __call__(self, req):
        if not self.memcache:
            self.memcache = cache_from_env(req.environ)
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
        # get token from cookie and query memcache
        token = req.cookies('_token')
        if self.memcache and token:
            cache_val = self.memcache.get(
                '%s_%s' % (self.title, token))
            if cache_val:
                self.token_bank[token] = cache_val
        status = self.token_bank.get(token, None)
        if status:
            storage_url = status.get('url', None)
        # login page
        if req.path == login_path:
            return self.page_login(req)
        if not token or not storage_url:
            return HTTPFound(location=login_path)
        self.token_bank[token].update({'last': time()})
        # clean up token bank
        for tok, val in self.token_bank.items():
            last = val.get('last', 0)
            if (time() - last) >= self.cookie_max_age:
                del(self.token_bank[tok])
        if 'X-PJAX' in req.headers:
            return self.pass_file(req, 'images/test.html',
                                  'text/html')
            # return self.page_cont_list(req, storage_url, token,
            #                            template_name='containers.tmpl')
            # return self.page_obj_list(req, storage_url, token,
            #                           template_name='objectss.tmpl')
        # ajax action
        if '_ajax' in req.params_alt():
            if req.params_alt()['_action'].endswith('_meta_list'):
                status, headers = self.action_routine(req, storage_url, token)
                return Response(status=status, body=headers)
            return Response(status=self.action_routine(req, storage_url,
                                                       token))
        # after action
        if '_action' in req.params_alt():
            if req.params_alt()['_action'] == 'logout':
                del self.token_bank[token]
                self.memcache.delete('%s_%s' % (self.title, token))
                return HTTPFound(location=login_path)
            return self.page_after_action(req, storage_url, token)
        # construct main pages
        return self.page_main(req, storage_url, token)

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
    
    def page_login(self, req):
        """ create login page """
        if req.method == 'POST':
            try:
                username = req.params_alt().get('username')
                password = req.params_alt().get('password')
                (storage_url, token) = get_auth(self.auth_url,
                                                username, password,
                                                auth_version=self.auth_version)
                if self.token_bank.get(token, None):
                    self.token_bank[token].update({'url': storage_url,
                                                   'last': int(time())})
                else:
                    self.token_bank[token] = {'url': storage_url,
                                              'last': int(time())}
                resp = HTTPFound(location=self.add_prefix(storage_url) + \
                                 '?limit=%s' % self.items_per_page)
                resp.set_cookie('_token', token, path=self.page_path,
                                max_age=self.cookie_max_age,
                                secure=self.secure)
                self.memcache_update(token)
                return resp
            except Exception, err:
                lang = self.get_lang(req)
                resp = Response(charset='utf8')
                resp.app_iter = self.tmpl({'ptype': 'login',
                                           'top': self.page_path,
                                           'title': self.title, 'lang': lang,
                                           'message': 'Login Failed'})
                return resp
        token = req.cookies('_token')
        status = self.token_bank.get(token, None) if token else None
        lang = self.get_lang(req)
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
        self.memcache_update(token)
        return resp

    def page_after_action(self, req, storage_url, token):
        """ page after action """
        path = urlparse(self.del_prefix(req.url)).path
        vrs, acc, cont, obj = split_path(path, 1, 4, True)
        path_type = len([i for i in [vrs, acc, cont, obj] if i])
        params = req.params_alt()
        loc = storage_url
        action = params.get('_action')
        if action == 'cont_create' or action == 'obj_create':
            rc = self.action_routine(req, storage_url, token)
            if rc == HTTP_CREATED:
                self.token_bank[token].update({'msg': 'Create Success'})
            elif rc == HTTP_BAD_REQUEST:
                self.token_bank[token].update({'msg': ''})
            elif rc == HTTP_PRECONDITION_FAILED:
                self.token_bank[token].update({'msg': 'Invalid name or too long.'})
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
           action == 'cont_acl' or action == 'obj_set_delete_time' or \
           action == 'cont_set_version' or action == 'cont_unset_version' or \
           action == 'cont_contsync':
            if self.action_routine(req, storage_url, token) == HTTP_ACCEPTED:
                result = 'Success'
            else:
                result = 'Failed'
            if action == 'cont_acl':
                self.token_bank[token].update(
                    {'msg': 'ACL update %s' % result})
            elif action == 'obj_set_delete_time':
                self.token_bank[token].update(
                    {'msg': 'Schedule of deletion update %s' % result})
            elif action == 'cont_set_version' or action == 'cont_unset_version':
                self.token_bank[token].update(
                    {'msg': 'Version-storing container update %s' % result})
            else:
                self.token_bank[token].update(
                    {'msg': 'Metadata update %s' % result})
            if action.startswith('cont_'):
                loc = storage_url
            else:
                loc = self.cont_path(path)
        resp = HTTPFound(location=self.add_prefix(loc))
        resp.set_cookie('_token', token, path=self.page_path,
                        max_age=self.cookie_max_age,
                        secure=self.secure)
        self.memcache_update(token)
        return resp

    def page_main(self, req, storage_url, token):
        """ main page container list or object list """
        path = urlparse(self.del_prefix(req.url)).path
        if len(path.split('/')) <= 2:
            path = urlparse(storage_url).path
        vrs, acc, cont, obj = split_path(path, 1, 4, True)
        path_type = len([i for i in [vrs, acc, cont, obj] if i])
        if path_type == 2:  # account
            return self.page_cont_list(req, storage_url, token)
        if path_type == 3:  # container
            return self.page_obj_list(req, storage_url, token)
        if path_type == 4:  # object
            try:
                (obj_status, objct) = get_object(storage_url, token, cont, obj)
            except ClientException, e:
                resp = Response(charset='utf8')
                resp.status = e.http_status
                return resp
            except err:
                pass
            resp = Response()
            resp.set_cookie('_token', token, path=self.page_path,
                            max_age=self.cookie_max_age,
                            secure=self.secure)
            resp.status = HTTP_OK
            resp.headers = obj_status
            resp.body = objct
            self.token_bank[token].update({'msg': ''})
            self.memcache_update(token)
            return resp
        return HTTPFound(location=self.add_prefix(storage_url))

    def page_cont_list(self, req, storage_url, token, template=None):
        """ """
        if template is None:
            tmpl = self.tmpl
        path = urlparse(self.del_prefix(req.url)).path
        if len(path.split('/')) <= 2:
            path = urlparse(storage_url).path
        vrs, acc, cont, obj = split_path(path, 1, 4, True)
        lang = self.get_lang(req)
        base = self.add_prefix(urlparse(storage_url).path)
        status = self.token_bank.get(token, None)
        msg = status.get('msg', '') if status else ''
        params = req.params_alt()
        limit = params.get('limit', self.items_per_page)
        marker = params.get('marker', '')
        end_marker = params.get('end_marker', '')
        delete_confirm = quote(params.get('delete_confirm', ''))
        acl_edit = quote(params.get('acl_edit', ''))
        meta_edit = quote(params.get('meta_edit', ''))
        contsync_edit = quote(params.get('contsync_edit', ''))
        # whole container list
        try:
            whole_cont_list = self._get_whole_cont_list(storage_url, token)
        except ClientException, err:
            resp = Response(charset='utf8')
            resp.status = err.http_status
            return resp
        # container list for one page
        try:
            (acct_status, cont_list) = get_account(storage_url, token,
                                                   limit=self.items_per_page,
                                                   marker=marker)
        except ClientException, err:
            resp = Response(charset='utf8')
            resp.status = err.http_status
            return resp
        cont_meta = {}
        cont_acl = {}
        cont_unquote_name = {}
        cont_version_cont = {}
        cont_sync_to = {}
        cont_sync_key = {}
        # pick only one container for confiming and editing
        edit_param = [acl_edit, delete_confirm, meta_edit, contsync_edit]
        if any(edit_param):
            edit_cont = filter(None, edit_param)[0]
            meta =  head_container(storage_url, token, edit_cont)
            cont_list = [{'name': edit_cont,
                          'count': meta.get('x-container-object-count'), 
                          'bytes': meta.get('x-container-bytes-used')}]
        # get matadata for each containers
        for i in cont_list:
            try:
                meta = head_container(storage_url, token, i['name'])
            except ClientException, err:
                resp = Response(charset='utf8')
                resp.status = e.http_status
                return resp
            cont_meta[i['name']] = dict(
                [(m[len('x-container-meta-'):].capitalize(), meta[m])
                 for m in meta.keys()
                 if m.startswith('x-container-meta')])
            cont_acl[i['name']] = dict(
                [(m[len('x-container-'):], meta[m])
                 for m in meta.keys()
                 if m.startswith('x-container-read')
                 or m.startswith('x-container-write')])
            cont_unquote_name[i['name']] = unquote(i['name'])
            if 'x-versions-location' in meta:
                cont_version_cont[i['name']] = unquote(meta.get('x-versions-location'))
            if 'x-container-sync-to' in meta:
                cont_sync_to[i['name']] = unquote(meta.get('x-container-sync-to'))
            if 'x-container-sync-key' in meta:
                cont_sync_key[i['name']] = unquote(meta.get('x-container-sync-key'))
        # calc marker for paging.
        (prev_marker, next_marker, last_marker) = self.paging_items(marker,
                                                                    whole_cont_list,
                                                                    self.items_per_page)
        # create page
        resp = Response(charset='utf8')
        resp.set_cookie('_token', token, path=self.page_path,
                        max_age=self.cookie_max_age,
                        secure=self.secure)
        resp.app_iter = tmpl({'ptype': 'containers',
                              'title': self.title,
                              'lang': lang,
                              'top': self.page_path,
                              'account': acc,
                              'message': msg,
                              'base': base,
                              'whole_containers': whole_cont_list,
                              'containers': cont_list,
                              'container_meta': cont_meta,
                              'container_acl': cont_acl,
                              'containers_unquote': cont_unquote_name,
                              'delete_confirm': delete_confirm,
                              'acl_edit': acl_edit,
                              'meta_edit': meta_edit,
                              'enable_versions': self.enable_versions,
                              'containers_version': cont_version_cont,
                              'enable_container_sync': self.enable_container_sync,
                              'contsync_edit': contsync_edit,
                              'cont_sync_to': cont_sync_to,
                              'cont_sync_key': cont_sync_key,
                              'limit': limit,
                              'prev_p': prev_marker,
                              'next_p': next_marker,
                              'last_p': last_marker,
                              'delimiter': '',
                              'prefix': ''})
        self.token_bank[token].update({'msg': ''})
        self.memcache_update(token)
        return resp

    def page_obj_list(self, req, storage_url, token, template=None):
        """ """
        if template is None:
            tmpl = self.tmpl
        path = urlparse(self.del_prefix(req.url)).path
        if len(path.split('/')) <= 2:
            path = urlparse(storage_url).path
        vrs, acc, cont, obj = split_path(path, 1, 4, True)
        lang = self.get_lang(req)
        base = self.add_prefix(urlparse(storage_url).path)
        status = self.token_bank.get(token, None)
        msg = status.get('msg', '') if status else ''
        params = req.params_alt()
        limit = params.get('limit', self.items_per_page)
        marker = params.get('marker', '')
        end_marker = params.get('end_marker', '')
        prefix = params.get('prefix', '')
        delete_confirm = quote(params.get('delete_confirm', ''))
        acl_edit = quote(params.get('acl_edit', ''))
        meta_edit = quote(params.get('meta_edit', ''))
        # whole container list
        try:
            whole_cont_list = self._get_whole_cont_list(storage_url, token)
        except ClientException, err:
            resp = Response(charset='utf8')
            resp.status = err.http_status
            return resp
        # whole object list, and object list for one page
        try:
            (cont_status, _whole_obj_list) = get_container(storage_url, token, cont,
                                                           delimiter=self.delimiter,
                                                           prefix=prefix,
                                                           full_listing=True)
            (cont_status, obj_list) = get_container(storage_url, token, cont,
                                                    limit=self.items_per_page,
                                                    delimiter=self.delimiter,
                                                    prefix=prefix, marker=marker)
        except ClientException, err:
            resp = Response(charset='utf8')
            resp.status = err.http_status
            return resp
        whole_obj_list = zip([_o.get('name',_o.get('subdir')) for _o in _whole_obj_list],
                             [unquote(_o.get('name', _o.get('subdir'))) for _o in _whole_obj_list])
        obj_meta = {}
        obj_unquote_name = {}
        obj_delete_set_time = {}
        path_type = len([i for i in [vrs, acc, cont, obj] if i])
        # pick only one object for confiming and editing
        edit_param = [delete_confirm, meta_edit]
        if any(edit_param):
            edit_obj = filter(None, edit_param)[0]
            obj_list = [obj for obj in _whole_obj_list if 'name' in obj and obj['name'] == edit_obj]
        # get matadata for each objects
        for i in obj_list:
            try:
                if 'subdir' in i:
                    mata = {}
                else:
                    meta = head_object(storage_url, token, cont, i['name'])
            except ClientException, err:
                resp = Response(charset='utf8')
                resp.status = err.http_status
                return resp
            if 'subdir' in i:
                obj_meta[i['subdir']] = {}
                obj_unquote_name[i['subdir']] = unquote(i['subdir'])
            else:
                obj_meta[i['name']] = dict(
                    [(m[len('x-object-meta-'):].capitalize(), meta[m])
                     for m in meta.keys() if m.startswith('x-object-meta')])
                obj_unquote_name[i['name']] = unquote(i['name'])
                if 'x-delete-at' in meta:
                    obj_delete_set_time[i['name']] = meta.get('x-delete-at')
        # calc markers for paging.
        (prev_marker, next_marker, last_marker) = self.paging_items(marker,
                                                                    whole_obj_list,
                                                                    self.items_per_page)
        # create page
        resp = Response(charset='utf8')
        resp.set_cookie('_token', token, path=self.page_path,
                        max_age=self.cookie_max_age,
                        secure=self.secure)
        base = '/'.join(base.split('/') + [cont])
        resp.app_iter = tmpl({'ptype': 'objects',
                              'title': self.title,
                              'lang': lang,
                              'top': self.page_path,
                              'account': acc,
                              'container': cont,
                              'container_unquote': unquote(cont),
                              'message': msg,
                              'base': base,
                              'whole_containers': whole_cont_list,
                              'objects': obj_list,
                              'object_meta': obj_meta,
                              'objects_unquote': obj_unquote_name, 
                              'delete_confirm': delete_confirm,
                              'acl_edit': acl_edit,
                              'meta_edit': meta_edit,
                              'delete_set_time': obj_delete_set_time,
                              'enable_object_expire': self.enable_object_expire,
                              'limit': limit,
                              'prev_p': prev_marker,
                              'next_p': next_marker,
                              'last_p': last_marker,
                              'delimiter': self.delimiter,
                              'prefix': prefix,
                              'icon_image': icon_image})
        self.token_bank[token].update({'msg': ''})
        self.memcache_update(token)
        return resp
    
    def memcache_update(self, token):
        """ """
        if self.memcache and self.token_bank.get(token, None):
            self.memcache.set('%s_%s' % (self.title, token), self.token_bank.get(token),
                              time=self.cookie_max_age)

    def get_lang(self, req):
        """ """
        return req.headers.get('Accept-Language', 'en,').split(',')[0].split(';')[0]

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
        query = ''
        if obj and self.delimiter in obj:
            prefix = self.delimiter.join(obj.split(self.delimiter)[:-1]) + self.delimiter
            query = 'delimiter=%s&prefix=%s' % (self.delimiter, prefix)
        path = '/' + vrs + '/' + acc + '/' + cont
        return urlunsplit((p.scheme, p.netloc, path, query, p.fragment))

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
        # to delete container metadata: set blank.
        # to delete object metadata: exclude meta to delete from existing metas.
        # I don't understand why use different way for the same purpose.
        for i in range(10):
            if 'container_meta_key%s' % i in form:
                keyname = form['container_meta_key%s' % i].lower()
                if len(keyname) > 128:
                    raise ValueError
                val = form.get('container_meta_val%s' % i)
                if not val:
                    continue
                if len(val) > 256:
                    raise ValueError
                headers.update({'x-container-meta-' + keyname: val})
                continue
            if 'object_meta_key%s' % i in form:
                keyname = form['object_meta_key%s' % i].lower()
                if len(keyname) > 128:
                    raise ValueError
                val = form.get('object_meta_val%s' % i)
                if not val:
                    continue
                if len(val) > 256:
                    raise ValueError
                headers.update({'x-object-meta-' + keyname: val})
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

    def contsync_check(self, form):
        """ """
        removing = [i[len('remove-'):]
                    for i in form.keys() if i.startswith('remove-')]
        headers = {}
        for sync in ['x-container-sync-to', 'x-container-sync-key']:
            headers.update({acl: form.get(sync, 'blank')})
            if sync in removing:
                headers.update({sync: ''})
            if headers[sync] == 'blank':
                del(headers[sync])
        return headers

    def get_current_meta(self, headers):
        """ """
        current_meta = {}
        for k in headers.keys():
            if k.startswith('x-container-meta-') or k.startswith('x-container-read-') or \
               k.startswith('x-container-write-') or k.startswith('x-versions-location') or \
               k.startswith('x-container-sync-to-') or k.startswith('x-container-sync-key') or \
               k.startswith('x-object-meta-') or k.startswith('x-delete-a'):
                current_meta.update({k: headers.get(k)})
            else:
                continue
        return current_meta

    def clean_blank_meta(self, meta):
        """ """
        current_meta = {}
        for k in meta.keys():
            if len(meta.get(k)) == 0:
                continue
            current_meta.update({k: meta.get(k)})
        return current_meta

    def paging_items(self, marker, whole_list, items_per_page):
        """ pick container or object names to use marker parameter."""
        # one page
        if len(whole_list) <= items_per_page:
            return ('', '', '')
        whole_len = len(whole_list)
        marker_index = range(items_per_page - 1, whole_len, items_per_page)
        whole_cont_names = [c for c, uc in whole_list]
        markers_list = [whole_cont_names[idx] for idx in marker_index]
        if not marker:
            # the first page
            prev_marker = ''
            next_marker = whole_cont_names[marker_index[0]]
        else:
            # over the 2nd page.
            if marker in markers_list:
                m_idx = markers_list.index(marker)
            else:
                near_markers = filter(lambda x: x > marker, markers_list)
                if len(near_markers) != 0:
                    m_idx = markers_list.index(near_markers[0])
                else:
                    m_idx = len(markers_list) - 1
            prev_marker = '' if m_idx <= 0 else markers_list[m_idx - 1]
            next_marker = markers_list[m_idx + 1 if (len(markers_list) - 1) > m_idx else -1]
        last_marker = markers_list[-1 if (whole_len - 1) != marker_index[-1] else -2]
        return (prev_marker, next_marker, last_marker)

    def _get_whole_cont_list(self, storage_url, token):
        """ whole container list """
        (_junk, _whole_cont_list) = get_account(storage_url, token,
                                                full_listing=True)
        return zip([_c['name'] for _c in _whole_cont_list],
                   [unquote(_c['name']) for _c in _whole_cont_list])

    def action_routine(self, req, storage_url, token):
        """ execute action """
        path = urlparse(self.del_prefix(req.url)).path
        vrs, acc, cont, obj = split_path(path, 1, 4, True)
        path_type = len([i for i in [vrs, acc, cont, obj] if i])
        params = req.params_alt()
        self.logger.debug('Received Params: %s' % params)
        action = params.get('_action')
        lines = int(params.get('_line', self.items_per_page))
        page = int(params.get('_page', 0))
        marker = str(params.get('_marker', ''))
        cont_param = params.get('cont_name', None)
        obj_prefix = params.get('obj_prefix', '')
        if cont_param:
            cont = quote(cont_param)
        obj_param = params.get('obj_name', None)
        if obj_param and len(obj_param) == 2:
            obj_name, obj_fp = obj_param
            if obj_prefix:
                obj_name = obj_prefix + obj_name
            obj = quote(obj_name)
        else:
            obj_name, obj_fp = ('', None)
        from_cont = params.get('from_container', None)
        if from_cont:
            cont = quote(from_cont)
        from_obj = params.get('from_object', None)
        if from_obj:
            obj = from_obj
        to_cont = params.get('to_container', None)
        if to_cont:
            to_cont = to_cont
        to_obj = params.get('to_object', None)
        if to_obj:
            to_obj = quote(to_obj)
        try:
            meta_headers = self.metadata_check(params)
        except ValueError, err:
            return HTTP_PRECONDITION_FAILED
        acl_headers = self.acl_check(params)
        obj_delete_set = params.get('obj_delete_time', None)
        version_cont = params.get('version_container', None)
        contsync_to = params.get('sync_to', None)
        contsync_key = params.get('sync_key', None)
        if version_cont:
            version_cont = quote(version_cont)
        unset_version = params.get('unset_version_container', None)

        if action == 'cont_list':
            (acct_status, cont_list) = get_account(storage_url,
                                                   token,
                                                   marker=marker,
                                                   limit=lines)
            #resp.app_iter = [json.dumps(c) for c in cont_list]
        if action == 'cont_create':
            if cont:
                if '/' in cont or len(cont) > 254:
                    return HTTP_PRECONDITION_FAILED
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
        if action == 'cont_metadata' or action == 'cont_acl' or \
           action == 'cont_set_version' or action == 'cont_unset_version' or \
           action == 'cont_contsync':
            headers = {}
            if meta_headers or acl_headers or version_cont or unset_version or \
               contsync_to or contsync_key:
                try:
                    headers = head_container(storage_url, token, cont)
                except ClientException, err:
                    return err.http_status
            if meta_headers:
                headers = self.get_current_meta(headers)
                headers.update(meta_headers)
            if acl_headers:
                headers = self.get_current_meta(headers)
                if acl_headers.get('x-container-read', None):
                    if not referrer_allowed('x-container-read',
                                            acl_headers.get('x-container-read')):
                        return HTTP_PRECONDITION_FAILED
                if acl_headers.get('x-container-write', None):
                    if not referrer_allowed('x-container-write',
                                            acl_headers.get('x-container-write')):
                        return HTTP_PRECONDITION_FAILED
                headers.update(acl_headers)
            if version_cont:
                headers = self.get_current_meta(headers)
                headers.update({'x-versions-location': version_cont})
            if unset_version:
                headers = self.get_current_meta(headers)
                headers.update({'x-versions-location': ''})
            if contsync_to and contsync_key:
                headers = self.get_current_meta(headers)
                headers.update({'x-container-sync-to': contsync_to})
                headers.update({'x-container-sync-key': contsync_key})
            if not headers:
                return HTTP_NO_CONTENT
            try:
                post_container(storage_url, token, cont, headers)
            except ClientException, err:
                return err.http_status
            return HTTP_ACCEPTED
        if action == 'cont_meta_list':
            headers = {}
            try:
                headers = head_container(storage_url, token, cont)
            except ClientException, err:
                return err.http_status, json.dumps(headers)
            return HTTP_OK, json.dumps(headers)
        if action == 'obj_meta_list':
            headers = {}
            try:
                headers = head_object(storage_url, token, cont, obj)
            except ClientException, err:
                return err.http_status, headers
            return HTTP_OK, headers
        if action == 'obj_create':
            if obj:
                if len(obj) > 1024:
                    return HTTP_PRECONDITION_FAILED
                obj_size = None
                if params.get('file_size'):
                    obj_size = int(params['file_size'])
                else:
                    try:
                        obj_fp.seek(0,2)
                        obj_size = obj_fp.tell()
                        obj_fp.seek(0,0)
                    except  IOError, err:
                        pass
                self.logger.debug('Upload obj size: %s' % obj_size)
                try:
                    put_object(storage_url, token, cont, obj, obj_fp, content_length=obj_size)
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
            if meta_headers:
                try:
                    headers = head_object(storage_url, token, cont, obj)
                except ClientException, err:
                    return err.http_status
                headers = self.get_current_meta(headers)
                headers.update(meta_headers)
                headers = self.clean_blank_meta(headers)
                # to delete object metadata: exclude meta to delete from existing metas.
            try:
                post_object(storage_url, token, cont, obj, headers)
            except ClientException, err:
                return err.http_status
            return HTTP_ACCEPTED
        if action == 'obj_copy':
            if not to_obj:
                to_obj = obj.split(self.delimiter)[-1]
            try:
                copy_object(storage_url, token, cont, obj,
                            to_cont, to_obj)
            except ClientException, err:
                return err.http_status
            return HTTP_CREATED
        if action == 'obj_set_delete_time':
            try:
                headers = head_object(storage_url, token, cont, obj)
            except ClientException, err:
                return err.http_status
            headers = self.get_current_meta(headers)
            if obj_delete_set:
                delete_time = str(int(mktime(strptime(obj_delete_set, '%Y-%m-%dT%H:%M'))))
            else:
                delete_time = ''
            headers.update({'x-delete-at': delete_time})
            # to delete object metadata: exclude meta to delete from existing metas.
            headers = self.clean_blank_meta(headers)
            try:
                post_object(storage_url, token, cont, obj, headers)
            except ClientException, err:
                return err.http_status
            return HTTP_ACCEPTED
        return HTTP_PRECONDITION_FAILED

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
        template_type = 'taylor.tmpl'
        try:
            tmpl = self.tmpls.get_template(template_type)
            return tmpl.render(**values)
        except:
            return exceptions.html_error_template().render()
