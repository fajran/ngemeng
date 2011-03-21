import re
import os
import hashlib
import codecs
from collections import defaultdict
from operator import attrgetter
from email.utils import parsedate
from datetime import datetime

import yaml
from jinja2 import Template, FileSystemLoader, Environment

class Config(object):
    @classmethod
    def read(cls, pyfile):
        var = {}
        execfile(pyfile, globals(), var)

        for key in var.keys():
            if key.upper() != key:
                del var[key]

        return cls(var)

    def __init__(self, conf=None):
        if conf is None:
            conf = {}
        self._conf = conf

    def __getattr__(self, name):
        return self._conf.get(name, None)

class Content(object):
    @classmethod
    def read(cls, f):
        if isinstance(f, basestring):
            f = codecs.open(f, encoding='utf-8')
        content = f.read()

        re_content = re.compile(r'\s*---(?P<meta>.+?)---\n(?P<content>.+)',
                                re.MULTILINE | re.DOTALL)

        m = re_content.match(content)
        if not m:
            raise ValueError, 'Invalid content format'

        meta, content = m.groups()
        meta = yaml.load(meta)

        return cls(content, **meta)

    def __init__(self, content, **meta):
        self.content = content
        self.meta = meta

        assert meta.has_key('title')
        assert meta.has_key('permalink')
        assert meta.has_key('date')

        self.parsed = None
        self.title = meta['title']
        self.permalink = meta['permalink']
        self.date = self._parse_date(meta['date'])

    def _parse_date(self, date):
        if isinstance(date, datetime):
            return date

        try:
            return datetime.strptime(date, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            dt = parsedate(date)
            if dt is None:
                raise ValueError, 'Invalid date: %s' % date
            return datetime(*dt[:6])

    def parse(self):
        if self.parsed is not None:
            return
        self.parsed = self._parse_rst()

    def _parse_rst(self):
        from docutils import core

        parts = core.publish_parts(self.content, writer_name='html')
        html = parts['fragment']

        # From http://techspot.zzzeek.org/2010/12/06/my-blogofile-hacks/
        code_block_re = re.compile(
            r"<pre class=\"literal-block\">\n"
            r"(?:#\!(?P<lang>\w+)\n)?"
            r"(?P<code>.*?)"
            r"</pre>", re.DOTALL
        )

        def repl(m):
            lang = m.group('lang')
            code = m.group('code')
            return '<pre class="literal-block"><code class="%s">%s</code></pre>' % (lang, code)

        return code_block_re.sub(repl, html)

class Printer(object):
    def __init__(self, outdir, templatedir, config=None):
        self.outdir = outdir
        self.templatedir = templatedir

        if config is None:
            config = Config()
        self.config = config

        self.loader = FileSystemLoader(templatedir)
        self.env = Environment(loader=self.loader)

    def write(self, target, context, template):
        target = target.lstrip('/')
        if target.endswith('/') or target == '':
            target = '%sindex.html' % target
        path = os.path.join(self.outdir, target)
        dirname = os.path.dirname(path)
        if not os.path.exists(dirname):
            os.makedirs(dirname)

        t = self.env.get_template(template)
        html = t.render(context)

        f = open(path, 'w')
        f.write(html)
        f.close()

        print '..', path

class BlogTag(object):
    def __init__(self, tag):
        self.tag = tag
        self.url = '/tags/%s/' % self.tag

    def __str__(self):
        return self.tag

class BlogEntry(object):
    def __init__(self, content):
        self.content = content.parsed
        self.date = content.date
        self.title = content.title
        self.tags = self._get_tags(content.meta.get('tags', None))

        date = content.date
        self.url = '/%04d/%02d/%02d/%s/' % (date.year, date.month,
                                            date.day,
                                            content.permalink)

        strid = '%04d-%02d-%02d-%s' % (date.year, date.month, date.day,
                                       content.permalink)
        self.id = hashlib.sha1(strid).hexdigest()

    def _get_tags(self, tags):
        if tags is None:
            return []
        return [BlogTag(tag.strip()) for tag in tags.split(',')]

class Blog(object):
    def __init__(self, contents, printer, config):
        self.contents = contents
        self.printer = printer
        self.config = config

    def get_default_context(self):
        return {'google_analytics_id': self.config.GOOGLE_ANALYTICS_ID}

    def get_context(self, extra):
        context = self.get_default_context()
        context.update(extra)
        return context

    def write(self):
        for c in self.contents:
            c.parse()
        self.contents = sorted(self.contents, key=attrgetter('date'))

        self._write_entries()
        # self._write_indices()
        self._write_index()

    def _write_entries(self):
        for c in self.contents:
            entry = BlogEntry(c)
            context = {'entry': entry,
                       'title': entry.title}
            context = self.get_context(context)
            self.printer.write(entry.url, context, 'blog_entry.html')

    def _write_indices(self):
        tree = {}
        for c in self.contents:
            entry = BlogEntry(c)

            year, month, day = c.date.timetuple()[:3]
            dt_year = datetime(year, 1, 1)
            dt_month = datetime(year, month, 1)
            dt_day = datetime(year, month, day)

            t_year = tree.get(dt_year, {'date': dt_year, 'months': {}})
            tree[dt_year] = t_year

            t_month = t_year['months'].get(dt_month, {'date': dt_month, 'days': {}})
            t_year['months'][dt_month] = t_month

            t_day = t_month['days'].get(dt_day, {'date': dt_day, 'entries': []})
            t_month['days'][dt_day] = t_day

            t_day['entries'].append(entry)

        for year, months in sorted(tree.items()):
            for month, days in sorted(months['months'].items()):
                for day, entries in sorted(days['days'].items()):
                    path = '%04d/%02d/%02d/' % \
                           (day.year, day.month, day.day)
                    context = {'date': day,
                               'entries': entries['entries']}
                    context = self.get_context(context)
                    self.printer.write(path, context, 'blog_daily.html')

                path = '%04d/%02d/' % (month.year, month.month)
                context = {'date': month,
                           'days': ((day, entries['entries'])
                                    for day, entries in sorted(days['days'].items()))}
                context = self.get_context(context)
                self.printer.write(path, context, 'blog_monthly.html')

            # path = '%04d/' % (year)
            # context = {'date': year,
            #            'days': ((day, entries['entries'])
            #                     for day, entries in sorted(days['days'].items()))}
            # context = self.get_context(context)
            # self.printer.write(path, context, 'blog_monthly.html')

    def _write_index(self):
        split = 10
        total = len(self.contents)
        pages = (total // split) + 1
        contents = list(reversed(self.contents))
        for index, start in enumerate(range(0, total, split)):
            path = 'index.html'
            if index > 0:
                path = 'index%s.html' % (index+1)

            context = {'entries': [BlogEntry(c)
                                   for c in contents[start:start+split]]}
            if index > 1:
                context['prev'] = 'index%s.html' % index
            elif index == 1:
                context['prev'] = 'index.html'
            if index < pages-1:
                context['next'] = 'index%s.html' % (index+2)
            context = self.get_context(context)
            self.printer.write(path, context, 'blog_index.html')

def main():
    config = Config.read('conf.py')

    files = os.listdir('_posts')
    contents = [Content.read(os.path.join('_posts', fname))
                for fname in files if fname.endswith('.rst')]
    p = Printer('_build', '_templates')
    b = Blog(contents, p, config=config)
    b.write()

if __name__ == '__main__':
    main()

