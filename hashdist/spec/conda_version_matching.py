from __future__ import absolute_import, division, print_function, unicode_literals

import operator as op
import re
import sys


CONDA_TARBALL_EXTENSION = '.tar.bz2'


def conda_match_eval(spec, pkg_dict):
    # MatchSpec is different from the command-line representation.  It assumes
    #    that spaces delineate version and build string.
    spec = re.sub(u'[><=!]+', u' \g<0>', spec)
    return _MatchSpec(spec).match(_Dist(**pkg_dict))


_version_check_re = re.compile(r'^[\*\.\+!_0-9a-z]+$')
_version_split_re = re.compile('([0-9]+|[*]+|[^0-9*]+)')
_version_cache = {}


PY3 = sys.version_info[0] == 3
if PY3:
    string_types = str
    from itertools import zip_longest
else:
    string_types = basestring
    from itertools import izip_longest as zip_longest


class _Dist(object):
    """Simplified copy of conda's Dist class"""
    def __init__(self, name, version, build_string, **kw):
        self.name = name
        self.version = version
        self.build_string = build_string

    @property
    def quad(self):
        return self.name, self.version, self.build_string, None


class _VersionOrder(object):
    """
    This class implements an order relation between version strings.
    Version strings can contain the usual alphanumeric characters
    (A-Za-z0-9), separated into components by dots and underscores. Empty
    segments (i.e. two consecutive dots, a leading/trailing underscore)
    are not permitted. An optional epoch number - an integer
    followed by '!' - can preceed the actual version string
    (this is useful to indicate a change in the versioning
    scheme itself). Version comparison is case-insensitive.

    Conda supports six types of version strings:

    * Release versions contain only integers, e.g. '1.0', '2.3.5'.
    * Pre-release versions use additional letters such as 'a' or 'rc',
      for example '1.0a1', '1.2.beta3', '2.3.5rc3'.
    * Development versions are indicated by the string 'dev',
      for example '1.0dev42', '2.3.5.dev12'.
    * Post-release versions are indicated by the string 'post',
      for example '1.0post1', '2.3.5.post2'.
    * Tagged versions have a suffix that specifies a particular
      property of interest, e.g. '1.1.parallel'. Tags can be added
      to any of the preceding four types. As far as sorting is concerned,
      tags are treated like strings in pre-release versions.
    * An optional local version string separated by '+' can be appended
      to the main (upstream) version string. It is only considered
      in comparisons when the main versions are equal, but otherwise
      handled in exactly the same manner.

    To obtain a predictable version ordering, it is crucial to keep the
    version number scheme of a given package consistent over time.
    Specifically,

    * version strings should always have the same number of components
      (except for an optional tag suffix or local version string),
    * letters/strings indicating non-release versions should always
      occur at the same position.

    Before comparison, version strings are parsed as follows:

    * They are first split into epoch, version number, and local version
      number at '!' and '+' respectively. If there is no '!', the epoch is
      set to 0. If there is no '+', the local version is empty.
    * The version part is then split into components at '.' and '_'.
    * Each component is split again into runs of numerals and non-numerals
    * Subcomponents containing only numerals are converted to integers.
    * Strings are converted to lower case, with special treatment for 'dev'
      and 'post'.
    * When a component starts with a letter, the fillvalue 0 is inserted
      to keep numbers and strings in phase, resulting in '1.1.a1' == 1.1.0a1'.
    * The same is repeated for the local version part.

    Examples:

        1.2g.beta15.rc  =>  [[0], [1], [2, 'g'], [0, 'beta', 15], [0, 'rc']]
        1!2.15.1_ALPHA  =>  [[1], [2], [15], [1, '_alpha']]

    The resulting lists are compared lexicographically, where the following
    rules are applied to each pair of corresponding subcomponents:

    * integers are compared numerically
    * strings are compared lexicographically, case-insensitive
    * strings are smaller than integers, except
    * 'dev' versions are smaller than all corresponding versions of other types
    * 'post' versions are greater than all corresponding versions of other types
    * if a subcomponent has no correspondent, the missing correspondent is
      treated as integer 0 to ensure '1.1' == '1.1.0'.

    The resulting order is:

           0.4
         < 0.4.0
         < 0.4.1.rc
        == 0.4.1.RC   # case-insensitive comparison
         < 0.4.1
         < 0.5a1
         < 0.5b3
         < 0.5C1      # case-insensitive comparison
         < 0.5
         < 0.9.6
         < 0.960923
         < 1.0
         < 1.1dev1    # special case 'dev'
         < 1.1a1
         < 1.1.0dev1  # special case 'dev'
        == 1.1.dev1   # 0 is inserted before string
         < 1.1.a1
         < 1.1.0rc1
         < 1.1.0
        == 1.1
         < 1.1.0post1 # special case 'post'
        == 1.1.post1  # 0 is inserted before string
         < 1.1post1   # special case 'post'
         < 1996.07.12
         < 1!0.4.1    # epoch increased
         < 1!3.1.1.6
         < 2!0.4.1    # epoch increased again

    Some packages (most notably openssl) have incompatible version conventions.
    In particular, openssl interprets letters as version counters rather than
    pre-release identifiers. For openssl, the relation

      1.0.1 < 1.0.1a   =>   True   # for openssl

    holds, whereas conda packages use the opposite ordering. You can work-around
    this problem by appending a dash to plain version numbers:

      1.0.1a  =>  1.0.1post.a      # ensure correct ordering for openssl
    """

    def __new__(cls, version):
        if isinstance(version, cls):
            return version
        self = _version_cache.get(version)
        if self is not None:
            return self
        self = _version_cache[version] = object.__new__(cls)

        # when fillvalue ==  0  =>  1.1 == 1.1.0
        # when fillvalue == -1  =>  1.1  < 1.1.0
        self.fillvalue = 0

        message = "Malformed version string '%s': " % version
        # version comparison is case-insensitive
        version = version.strip().rstrip().lower()
        # basic validity checks
        if version == '':
            raise ValueError("Empty version string.")
        invalid = not _version_check_re.match(version)
        if invalid and '-' in version and '_' not in version:
            # Allow for dashes as long as there are no underscores
            # as well, by converting the former to the latter.
            version = version.replace('-', '_')
            invalid = not _version_check_re.match(version)
        if invalid:
            raise ValueError(message + "invalid character(s).")
        self.norm_version = version

        # find epoch
        version = version.split('!')
        if len(version) == 1:
            # epoch not given => set it to '0'
            epoch = ['0']
        elif len(version) == 2:
            # epoch given, must be an integer
            if not version[0].isdigit():
                raise ValueError(message + "epoch must be an integer.")
            epoch = [version[0]]
        else:
            raise ValueError(message + "duplicated epoch separator '!'.")

        # find local version string
        version = version[-1].split('+')
        if len(version) == 1:
            # no local version
            self.local = []
        elif len(version) == 2:
            # local version given
            self.local = version[1].replace('_', '.').split('.')
        else:
            raise ValueError(message + "duplicated local version separator '+'.")

        # split version
        self.version = epoch + version[0].replace('_', '.').split('.')

        # split components into runs of numerals and non-numerals,
        # convert numerals to int, handle special strings
        for v in (self.version, self.local):
            for k in range(len(v)):
                c = _version_split_re.findall(v[k])
                if not c:
                    raise ValueError(message + "empty version component.")
                for j in range(len(c)):
                    if c[j].isdigit():
                        c[j] = int(c[j])
                    elif c[j] == 'post':
                        # ensure number < 'post' == infinity
                        c[j] = float('inf')
                    elif c[j] == 'dev':
                        # ensure '*' < 'DEV' < '_' < 'a' < number
                        # by upper-casing (all other strings are lower case)
                        c[j] = 'DEV'
                if v[k][0].isdigit():
                    v[k] = c
                else:
                    # components shall start with a number to keep numbers and
                    # strings in phase => prepend fillvalue
                    v[k] = [self.fillvalue] + c
        return self

    def __str__(self):
        return self.norm_version

    def _eq(self, t1, t2):
        for v1, v2 in zip_longest(t1, t2, fillvalue=[]):
            for c1, c2 in zip_longest(v1, v2, fillvalue=self.fillvalue):
                if c1 != c2:
                    return False
        return True

    def __eq__(self, other):
        return (self._eq(self.version, other.version) and
                self._eq(self.local, other.local))

    def startswith(self, other):
        # Tests if the version lists match up to the last element in "other".
        if other.local:
            if not self._eq(self.version, other.version):
                return False
            t1 = self.local
            t2 = other.local
        elif other.version:
            t1 = self.version
            t2 = other.version
        else:
            return True
        nt = len(t2) - 1
        if not self._eq(t1[:nt], t2[:nt]):
            return False
        v1 = [] if len(t1) <= nt else t1[nt]
        v2 = t2[nt]
        nt = len(v2) - 1
        if not self._eq([v1[:nt]], [v2[:nt]]):
            return False
        c1 = self.fillvalue if len(v1) <= nt else v1[nt]
        c2 = v2[nt]
        if isinstance(c2, string_types):
            return isinstance(c1, string_types) and c1.startswith(c2)
        return c1 == c2

    def __ne__(self, other):
        return not (self == other)

    def __lt__(self, other):
        for t1, t2 in zip([self.version, self.local], [other.version, other.local]):
            for v1, v2 in zip_longest(t1, t2, fillvalue=[]):
                for c1, c2 in zip_longest(v1, v2, fillvalue=self.fillvalue):
                    if c1 == c2:
                        continue
                    elif isinstance(c1, string_types):
                        if not isinstance(c2, string_types):
                            # str < int
                            return True
                    elif isinstance(c2, string_types):
                            # not (int < str)
                            return False
                    # c1 and c2 have the same type
                    return c1 < c2
        # self == other
        return False

    def __gt__(self, other):
        return other < self

    def __le__(self, other):
        return not (other < self)

    def __ge__(self, other):
        return not (self < other)


# This RE matches the operators '==', '!=', '<=', '>=', '<', '>'
# followed by a version string. It rejects expressions like
# '<= 1.2' (space after operator), '<>1.2' (unknown operator),
# and '<=!1.2' (nonsensical operator).
_version_relation_re = re.compile(r'(==|!=|<=|>=|<|>)(?![=<>!])(\S+)$')
_regex_split_re = re.compile(r'(\^\S+?\$)')
_regex_split_converter = {
    '|': 'any',
    ',': 'all',
}
_opdict = {'==': op.__eq__, '!=': op.__ne__, '<=': op.__le__,
           '>=': op.__ge__, '<': op.__lt__, '>': op.__gt__}


class _VersionSpec(object):
    def exact_match_(self, vspec):
        return self.spec == vspec

    def regex_match_(self, vspec):
        return bool(self.regex.match(vspec))

    def veval_match_(self, vspec):
        return self.op(_VersionOrder(vspec), self.cmp)

    def all_match_(self, vspec):
        return all(s.match(vspec) for s in self.spec[1])

    def any_match_(self, vspec):
        return any(s.match(vspec) for s in self.spec[1])

    def triv_match_(self, vspec):
        return True

    def __new__(cls, spec):
        if isinstance(spec, cls):
            return spec
        self = object.__new__(cls)
        self.spec = spec
        if isinstance(spec, tuple):
            self.match = self.all_match_ if spec[0] == 'all' else self.any_match_
        elif _regex_split_re.match(spec):
            m = _regex_split_re.match(spec)
            first = m.group()
            operator = spec[m.end()] if len(spec) > m.end() else None
            if operator is None:
                self.spec = first
                self.regex = re.compile(spec)
                self.match = self.regex_match_
            else:
                return _VersionSpec((_regex_split_converter[operator],
                                    tuple(_VersionSpec(s) for s in (first, spec[m.end()+1:]))))
        elif '|' in spec:
            return _VersionSpec(('any', tuple(_VersionSpec(s) for s in spec.split('|'))))
        elif ',' in spec:
            return _VersionSpec(('all', tuple(_VersionSpec(s) for s in spec.split(','))))
        elif spec.startswith(('=', '<', '>', '!')):
            m = _version_relation_re.match(spec)
            if m is None:
                raise ValueError("Invalid spec: %s" % spec)
            op, b = m.groups()
            self.op = _opdict[op]
            self.cmp = _VersionOrder(b)
            self.match = self.veval_match_
        elif spec == '*':
            self.match = self.triv_match_
        elif '*' in spec.rstrip('*'):
            self.spec = spec
            rx = spec.replace('.', r'\.')
            rx = rx.replace('+', r'\+')
            rx = rx.replace('*', r'.*')
            rx = r'^(?:%s)$' % rx
            self.regex = re.compile(rx)
            self.match = self.regex_match_
        elif spec.endswith('*'):
            self.op = _VersionOrder.startswith
            self.cmp = _VersionOrder(spec.rstrip('*').rstrip('.'))
            self.match = self.veval_match_
        else:
            self.match = self.exact_match_
        return self

    def str(self, inand=False):
        s = self.spec
        if isinstance(s, tuple):
            newand = not inand and s[0] == all
            inand = inand and s[0] == any
            s = (',' if s[0] == 'all' else '|').join(x.str(newand) for x in s[1])
            if inand:
                s = '(%s)' % s
        return s

    def is_exact(self):
        return self.match == self.exact_match_

    def __str__(self):
        return self.str()

    def __repr__(self):
        return "VersionSpec('%s')" % self.str()

    def __and__(self, other):
        if not isinstance(other, _VersionSpec):
            other = _VersionSpec(other)
        return _VersionSpec((all, (self, other)))

    def __or__(self, other):
        if not isinstance(other, _VersionSpec):
            other = _VersionSpec(other)
        return _VersionSpec((any, (self, other)))


class _MatchSpec(object):
    def __new__(cls, spec, target=Ellipsis, optional=Ellipsis, normalize=False):
        if isinstance(spec, cls):
            if target is Ellipsis and optional is Ellipsis and not normalize:
                return spec
            target = spec.target if target is Ellipsis else target
            optional = spec.optional if optional is Ellipsis else optional
            spec = spec.spec
        self = object.__new__(cls)
        self.target = None if target is Ellipsis else target
        self.optional = False if optional is Ellipsis else bool(optional)
        spec, _, oparts = spec.partition('(')
        if oparts:
            if oparts.strip()[-1] != ')':
                raise ValueError("Invalid MatchSpec: %s" % spec)
            for opart in oparts.strip()[:-1].split(','):
                if opart == 'optional':
                    self.optional = True
                elif opart.startswith('target='):
                    self.target = opart.split('=')[1].strip()
                else:
                    raise ValueError("Invalid MatchSpec: %s" % spec)
        spec = self.spec = spec.strip()
        parts = (spec,) if spec.endswith(CONDA_TARBALL_EXTENSION) else spec.split()
        nparts = len(parts)
        assert 1 <= nparts <= 3, repr(spec)
        self.name = parts[0]
        if nparts == 1:
            self.match_fast = self._match_any
            self.strictness = 1
            return self
        self.strictness = 2
        vspec = _VersionSpec(parts[1])
        if vspec.is_exact():
            if nparts > 2 and '*' not in parts[2]:
                self.version, self.build = parts[1:]
                self.match_fast = self._match_exact
                self.strictness = 3
                return self
            if normalize and not parts[1].endswith('*'):
                parts[1] += '*'
                vspec = _VersionSpec(parts[1])
                self.spec = ' '.join(parts)
        self.version = vspec
        if nparts == 2:
            self.match_fast = self._match_version
        else:
            rx = r'^(?:%s)$' % parts[2].replace('*', r'.*')
            self.build = re.compile(rx)
            self.match_fast = self._match_full
        return self

    def is_exact(self):
        return self.match_fast == self._match_exact

    def is_simple(self):
        return self.match_fast == self._match_any

    def _match_any(self, version, build):
        return True

    def _match_version(self, version, build):
        return self.version.match(version)

    def _match_exact(self, version, build):
        return build == self.build and self.version == version

    def _match_full(self, version, build):
        return self.build.match(build) and self.version.match(version)

    def match(self, dist):
        # type: (Dist) -> bool
        name, version, build, _ = dist.quad
        if name != self.name:
            return False
        result = self.match_fast(version, build)
        return result

    def __hash__(self):
        return hash(self.spec)

    def __repr__(self):
        return "MatchSpec('%s')" % self.__str__()

    def __str__(self):
        res = self.spec
        if self.optional or self.target:
            args = []
            if self.optional:
                args.append('optional')
            if self.target:
                args.append('target='+self.target)
            res = '%s (%s)' % (res, ','.join(args))
        return res
