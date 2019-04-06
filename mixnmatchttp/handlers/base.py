#  from __future__ import unicode_literals
from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from builtins import dict
from builtins import super
from builtins import int
from builtins import str
from future import standard_library
standard_library.install_aliases()
import logging
import http.server
import re
import urllib
import json
import base64, binascii
from wrapt import decorator
from string import Template
from future.utils import with_metaclass

from .. import endpoints
from ..common import abspath, param_dict, DictNoClobber

_logger = logging.getLogger(__name__)

@decorator
def methodhandler(realhandler, self, args, kwargs):
    '''Decorator for do_{HTTP METHOD} handlers
    
    Sets the canonical pathname, query and body; checks if the request
    is allowed, and if it's for an endpoint.
    Calls the endpoint's handler or the HTTP method handler
    '''

    _logger.debug('INIT for method handler')
    _logger.debug('Path is {}'.format(self.path))

    # split query from pathname and decode them
    #TODO other encodings??
    # take only the first set of parameters (i.e. everything
    # between the first ? and the subsequent / or #
    m = re.match('(/[^\?]*)(?:\?([^/#]*))?(.*)', self.path)
    query_str = urllib.parse.unquote_plus(m.group(2)) if m.group(2) else ''
    self._BaseHTTPRequestHandler__raw_pathname = \
            self._BaseHTTPRequestHandler__pathname = \
            urllib.parse.unquote_plus(m.group(1) + m.group(3))

    _logger.debug('Decoded path is {}'.format(self.pathname))
    # canonicalize the path
    self._BaseHTTPRequestHandler__pathname = abspath(self.pathname)
    _logger.debug('Real path is {}'.format(self.pathname))
    assert self.pathname[0] == '/'

    # save query parameters
    self._BaseHTTPRequestHandler__query = param_dict(query_str, itemsep='&',
            values_are_opt=True)
    _logger.debug('Query params are {}'.format(self.query))

    self._BaseHTTPRequestHandler__can_read_body = True
    self._BaseHTTPRequestHandler__body = None
    self._BaseHTTPRequestHandler__allowed_methods = None # use default
    self._BaseHTTPRequestHandler__read_body()

    # save content-type and body parameters
    try:
        self._BaseHTTPRequestHandler__decode_body()
    except DecodingError as e:
        self.send_error(400, explain=str(e))
        return

    self.show()
    # check if it's forbidden
    err = self.denied()
    if err is not None:
        self.send_error(*err)
        return

    # check if it's a special endpoint
    try:
        ep = self.endpoints.parse(self)
    except endpoints.NotAnEndpointError as e:
        _logger.debug('{}'.format(str(e)))
        realhandler()
    except endpoints.MethodNotAllowedError as e:
        _logger.debug('{}'.format(str(e)))
        self.send_error(405)
    except (endpoints.MissingArgsError, endpoints.ExtraArgsError) as e:
        _logger.debug('{}'.format(str(e)))
        self.send_error(404, explain=str(e))
    else:
        self._BaseHTTPRequestHandler__allowed_methods = \
                ep.allowed_methods
        if self.command == 'OPTIONS':
            _logger.debug('Doing OPTIONS')
            realhandler()
        else:
            _logger.debug('Calling endpoint handler')
            ep.handler()

######################### EXCEPTIONS ########################

class PageReadError(Exception):
    '''Base class for exceptions related to request body read'''
    pass

class UnsupportedOperationError(PageReadError):
    '''Exception raised when request body is read more than once'''

    def __init__(self):
        super(UnsupportedOperationError, self).__init__(
            'Cannot read body data again, buffer not seekable')

class DecodingError(PageReadError):
    '''Exception raised when cannot decode sent data'''
    pass

############################################################
class BaseMeta(type):
    '''Metaclass for BaseHTTPRequestHandler
    
    Adds each of the parents' endpoints, templates and template pages
    '''

    def __new__(cls, name, bases, attrs):
        new_class = super(BaseMeta, cls).__new__(cls, name, bases, attrs)

        _logger.debug('New class {}; has _endpoints: {}; bases: {}'.format(
            name, '_endpoints' in attrs, [b.__name__ for b in bases]))
        # every child gets it's own class attribute for _endpoints,
        # _template_pages and _templates, which combines all parents'
        # attributes
        required_classes = {
                '_endpoints': endpoints.Endpoint,
                '_template_pages': DictNoClobber,
                '_templates': DictNoClobber,
                }
        for attr in ['_endpoints', '_template_pages', '_templates']:
            for bc in bases[::-1]:
                if hasattr(bc, attr):
                    try:
                        dic = getattr(new_class, attr)
                    except AttributeError:
                        dic = required_classes[attr]()
                        setattr(new_class, attr, dic)
                    if not isinstance(dic, required_classes[attr]):
                        dic = required_classes[attr](dic)
                    dic.update_noclob(getattr(bc, attr))
            _logger.debug('Final {} for {}: {}'.format(
                attr, name, list(getattr(new_class, attr).keys())))

        return new_class

class BaseHTTPRequestHandler(with_metaclass(BaseMeta, http.server.SimpleHTTPRequestHandler, object)):
    _endpoints = endpoints.Endpoint()
    _template_pages = DictNoClobber(
        default={
            'data': '''
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="UTF-8" />
    $HEAD
    </head>
    <body>
    $BODY
    </body>
    </html>
    ''',
            'type': 'text/html'
            },
        )
    _templates = DictNoClobber()

    # copy the class attributes to instance ones, since Endpoint and
    # dicts are mutable
    # the alternative solution of setting it in __init__ would require
    # checking if such an attribute already exists (set by a child
    # class), then update_noclob, before calling the parent's __init__
    # method
    # we can't call parent's __init__ before updating endpoints, since
    # BaseHTTPRequestHandler's __init__ calls do_{METHOD} and all
    # attributes of child classes need to be set by that time
    @property
    def endpoints(self):
        '''Property the class' endpoints
        
        Copies the endpoints to an instance attribute the first time
        it is accessed so they can be modified for the instance
        '''

        if self._endpoints is self.__class__._endpoints:
            self._endpoints = self._endpoints.copy()
        return self._endpoints

    @endpoints.setter
    def endpoints(self, value):
        self._endpoints = value

    @property
    def templates(self):
        '''Property the class' template
        
        Copies the templates to an instance attribute the first time
        it is accessed so they can be modified for the instance
        '''

        if self._templates is self.__class__._templates:
            self._templates = self._templates.copy()
        return self._templates

    @templates.setter
    def templates(self, value):
        self._templates = value

    @property
    def template_pages(self):
        '''Property the class' template pages
        
        Copies the template pages to an instance attribute the first
        time it is accessed so they can be modified for the
        instance
        '''

        if self._template_pages is self.__class__._template_pages:
            self._template_pages = self._template_pages.copy()
        return self._template_pages

    @template_pages.setter
    def template_pages(self, value):
        self._template_pages = value

    def __init__(self, *args, **kwargs):
        _logger.debug('INIT for {}'.format(self))
        self.__pathname = ''
        self.__raw_pathname = ''
        self.__query = dict()
        self.__can_read_body = True
        self.__body = None
        self.__ctype = None
        self.__params = None
        self.__allowed_methods = None
        super(BaseHTTPRequestHandler, self).__init__(*args, **kwargs)

    @property
    def raw_pathname(self):
        '''Property for the request's pathname before canonicalization'''

        return self.__raw_pathname

    @property
    def pathname(self):
        '''Property for the request's pathname'''

        return self.__pathname

    @property
    def body(self):
        '''Property for the decoded request's body
        
        Raises a UnicodeDecodeError if we can't decode
        '''

        try:
            body = self.__body.decode('utf-8')
        except UnicodeDecodeError:
            _logger.debug('Errors decoding request body')
            body = self.__body.decode('utf-8',
                    errors='backslashreplace')
        return body

    @property
    def ctype(self):
        '''Property for the request's Content-Type'''

        return self.__ctype

    @property
    def params(self):
        '''Property for the request's body parameters'''

        return self.__params

    @property
    def allowed_methods(self):
        '''Property for the request's allowed methods'''

        return self.__allowed_methods

    @property
    def query(self):
        '''Property for the request's query dictionary'''

        return self.__query

    def get_param(self, parname, dic=None):
        '''Returns the value of parname inside dic
        
        dic is a dictionary, if None, then the body paramaters are
        checked first, then the URL parameters
        '''

        if dic is None:
            dic = self.__query
            dic.update(self.__params)
        try:
            value = dic[parname]
        except KeyError:
            return None

        return value

    def form_params(self, post_data=None):
        '''Parameter loader
        
        Returns a dictionary read from an
        application/x-www-form-urlencoded form
        post_data defaults to the request body
        '''

        _logger.debug('Loading parameters from form body')
        if post_data is None:
            post_data = self.body
        req_params = param_dict(post_data, itemsep='&')
        if not req_params:
            raise DecodingError('Cannot load parameters from request!')
        return req_params

    def JSON_params(self, post_data=None):
        '''Parameter loader
        
        Returns a dictionary read from a JSON string
        post_data defaults to the request body
        '''

        _logger.debug('Loading parameters from JSON body')
        if post_data is None:
            post_data = self.body
        try:
            req_params = json.loads(post_data)
        except JSONDecodeError:
            raise DecodingError('Cannot decode JSON!')
        return req_params

    def denied(self):
        '''Child class overrides this
        
        Returns None if allowed or a tuple(code, message, explain)
        '''

        return None

    def no_cache(self):
        '''Child overrides this
        
        Returns True or False if the response should not be cached by
        the browser
        '''

        return False

    def send_cache_control(self):
        '''Sends a no-caching directive if no_cache returns True'''

        if self.no_cache():
            self.send_header('Cache-Control',
                'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')

    def send_custom_headers(self):
        '''Child overrides this'''
        pass

    def send_headers(self, headers):
        '''Sends multiple headers'''

        for h,v in headers.items():
            self.send_header(h, v)

    def begin_response_goto(self, code=302, url=None, headers={}):
        '''Starts a redirection response
        
        code: HTTP code
        url: Location header; if None, then the goto URL parameter is
        taken; if that one is missing, then the response code is 200
        headers: additional headers to send
        '''

        if url is None:
            url = self.get_param('goto')
        if url is not None:
            self.send_response(code)
            self.send_header('Location', urllib.parse.unquote_plus(url))
        else:
            self.send_response(200)
        self.send_headers(headers)

    def send_response_goto(self, *args, **kwargs):
        '''Wrapper around begin_response_goto and end_response_default'''
        self.begin_response_goto(*args, **kwargs)
        self.end_response_default()

    def send_response_default(self, *args, **kwargs):
        '''Alias for send_response_empty at the moment'''

        return self.send_response_empty(*args, **kwargs)

    def send_response_empty(self, code=200, headers={}):
        '''Send an empty response
        
        headers: additional headers to send
        '''

        self.send_response(code)
        self.send_headers(headers)
        self.end_response_empty()

    def end_response_default(self):
        '''Alias for end_response_empty at the moment'''

        return self.end_response_empty()

    def end_response_empty(self):
        '''Ends an empty response'''

        #  self.send_header('Content-type', 'text/plain')
        self.send_header('Content-Length', 0)
        self.end_headers()

    def show(self):
        '''Logs the request'''

        _logger.info('''
----- Request Start ----->

{}
{}
{}

<----- Request End -----
'''.format(self.requestline, self.headers, self.body))

    def render(self, page, code=200, headers={}):
        '''Renders a page
        
        page: a dictionary with the following items:
            - data: the content of the page
            - type: the content type
        headers: additional headers to send
        '''

        self.send_response(code)
        self.send_header('Content-type', page['type'])
        self.send_header('Content-Length', len(page['data']))
        self.send_headers(headers)
        self.end_headers()
        self.wfile.write(page['data'])

    def page_from_template(self, template, dynfields={}):
        '''Returns a page from the given template'''

        try:
            page = self._template_pages[template['page']].copy()
        except KeyError:
            _logger.debug('Using default template page')
            page = self._template_pages['default'].copy()

        try:
            fields = template['fields']
        except KeyError:
            _logger.debug('No fields for template')
            fields = {}

        page['data'] = Template(page['data']).safe_substitute(fields)

        # it's allowed to have the same field in the template as well
        # as in the page's field values (e.g. fields['BODY'] also has
        # '$BODY' in there. The second one will be replaced with the
        # value from dynfields; so don't coalesce fields and dynfields
        # together
        page['data'] = Template(page['data']).safe_substitute(dynfields)

        # remove unused fields and encode
        page['data'] = re.sub('\$[a-zA-Z0-9_]+', '', page['data'])
        try:
            page['data'] = page['data'].encode('utf-8')
        except UnicodeEncodeError:
            _logger.debug('Errors encoding page body')
            page['data'] = page['data'].encode('utf-8',
                    errors='backslashreplace')

        return page

    def __read_body(self):
        '''Sets __body to the body data
        
        methodhandler calls this and it cannot be called again
        '''

        _logger.debug('Decoding body')
        if not self.__can_read_body:
            raise UnsupportedOperationError

        try:
            length = int(self.headers.get('Content-Length'))
        except TypeError:
            self.__body = b''
        else:
            self.__body = self.rfile.read(length)

        _logger.debug('Read {} bytes from body'.format(len(self.__body)))
        self.__can_read_body = False

    def __decode_body(self):
        '''Decodes the request, sets __ctype and __params appropriately
        
        __ctype is the Content-Type and __params is a dictionary of
        parameters. If Content-Type is neither JSON nor URL-encoded
        form, __params is empty
        raises DecodingError on failure
        '''

        ctype = self.headers.get('Content-Type')
        try:
            ctype = ctype.split(';',1)[0]
        except AttributeError:
            # No Content-Type
            if self.__body:
                raise DecodingError(
                    'Missing Content-Type with non-empty body')
            ctype = None
        if ctype in ['application/json', 'text/json']:
            param_loader = self.JSON_params
        elif ctype == 'application/x-www-form-urlencoded':
            param_loader = self.form_params
        else:
            _logger.debug("Don't know how to read body parameters")
            param_loader = lambda: {}

        self.__params = param_loader()
        self.__ctype = ctype
        _logger.debug('Request parameters: {}'.format(self.__params))

    @staticmethod
    def url_data(data_enc):
        '''Data decoder
        
        Returns the percent-decoded data
        '''

        try:
            data = urllib.parse.unquote_plus(data_enc)
        except: # what exception does it throw???
            raise DecodingError('Cannot URL decode request data!')
        return data

    @staticmethod
    def b64_data(data_enc):
        '''Data decoder
        
        Returns the base64-decoded data
        '''

        try:
            data = base64.b64decode(data_enc)
        except (TypeError,binascii.Error):
            raise DecodingError('Cannot Base64 decode request data!')
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            _logger.debug('Errors decoding base64 data')
            return data.decode('utf-8', errors='backslashreplace')

    def end_headers(self):
        '''Calls send_custom_headers'''

        self.send_custom_headers()
        self.send_cache_control()
        super(BaseHTTPRequestHandler, self).end_headers()

    def send_error(self, code, message=None, explain=None):
        '''Calls parent's send_error with the correct signature
        
        In python2, send_error does not accept the explain keyword
        argument
        '''

        try:
            super(BaseHTTPRequestHandler, self).send_error(code, message=message, explain=explain)
        except TypeError:
            super(BaseHTTPRequestHandler, self).send_error(code, message=message)

    def do_default(self, ep):
        '''Default handler for endpoints'''

        self.send_response_default()

    @methodhandler
    def do_GET(self):
        '''Decorated by methodhandler'''

        super(BaseHTTPRequestHandler, self).do_GET()

    @methodhandler
    def do_POST(self):
        '''Decorated by methodhandler'''

        super(BaseHTTPRequestHandler, self).do_GET()

    @methodhandler
    def do_OPTIONS(self):
        '''Decorated by methodhandler'''

        self.send_response_empty()

    @methodhandler
    def do_HEAD(self):
        '''Decorated by methodhandler'''

        super(BaseHTTPRequestHandler, self).do_HEAD()