import logging
import mechanize
import urllib
from urlparse import urlsplit
import os
import re
from hashlib import md5
from BeautifulSoup import BeautifulSoup


# Some sentinel values.
class TerminusSite(object):
    """Sentinel value to indicate that the next/previous site has not been
    loaded.
    """
    pass


class ParsingError(StandardError):
    pass


class StripError(StandardError):
    pass



class StripSiteBase(object):
    comicname = NotImplemented
    baseurl = NotImplemented

    def __init__(self, siteurl, imgurl, prevurl, nexturl, title=None):
        self.url = siteurl
        self.img = imgurl
        self.prev = prevurl
        self.next = nexturl
        self.title = title

    def __str__(self):
        return "<{s.comicname} {s.url} prev:{s.prev} next:{s.next}>".format(s=self)

    @classmethod
    def mkFromResponse(cls, resp):
        raise NotImplementedError("mkFromResponse must be overwritten.")

    @property
    def savename(self):
        return md5(self.img).hexdigest()

    @staticmethod
    def _absolute_url(base, anchor=None, img=None):
        if anchor is None and img is None:
            return None
        elif anchor is not None:
            return mechanize.Link(base, anchor["href"], anchor.text, "a", anchor.attrMap).absolute_url
        elif img is not None:
            return mechanize.Link(base, img["src"], img.text, "img", img.attrMap).absolute_url


class IncidentalComics(StripSiteBase):
    comicname = "Incidental Comics"
    baseurl = "http://www.gocomics.com/incidentalcomics/"

    @classmethod
    def mkFromResponse(cls, resp):
        tree = BeautifulSoup(resp.get_data())

        # imgurl = tree.find("img", attrs={"class":"strip"}).attrMap["src"]
        # imgurl = tree.find("img", attrs={"class":"strip"})["src"]
        imgurl = tree.find("img", attrs={"class":"strip"})["src"]

        navbar = tree.find("ul", attrs={"class":"feature-nav"})
        prev = cls._absolute_url(resp.geturl(),
                    anchor=navbar.findChild("a", attrs={"class":"prev"}))
        next = cls._absolute_url(resp.geturl(),
                    anchor=navbar.findChild("a", attrs={"class":"next"}))
        return cls(resp.geturl(), imgurl, prev, next)


class XKCD(StripSiteBase):
    comicname = "xkcd"
    baseurl = "http://www.xkcd.com/"

    comic_src_re = re.compile(r"http://imgs\.xkcd\.com/comics/.*")

    @classmethod
    def mkFromResponse(cls, resp):
        tree = BeautifulSoup(resp.get_data())
        title = tree.find("title").text
        imgs = tree.findAll("img", attrs={"src" : cls.comic_src_re})
        if len(imgs) != 1:
            raise ParsingError("%s: Unexpected number of comic <img>s found" % cls.__name__)
        imgurl = imgs[0]["src"]
        # imgurl = imgs[0].attrMap["src"]

        prevs = tree.findAll(lambda tag: tag.name == "a" and tag.text == "&lt; Prev")
        if len(prevs) == 0:
            prev = None
        elif len(prevs) != 2:
            raise ParsingError("%s: Number of prev links != 2" % cls.__name__)
        else:
            prev = cls._absolute_url(resp.geturl(), anchor=prevs[0])

        nexts = tree.findAll(lambda tag: tag.name == "a" and tag.text == "Next &gt;")
        if len(nexts) == 0:
            next = None
        elif len(nexts) != 2:
            raise ParsingError("%s: Number of next links != 2" % cls.__name__)
        else:
            next = cls._absolute_url(resp.geturl(), anchor=nexts[0])
            # Exclude wronglye recognized `next` links.
            if next == (resp.geturl() + "#"):
                next = None

        return cls(resp.geturl(), imgurl, prev, next, title)

    @property
    def savename(self):
        return urlsplit(self.img).path.split("/")[-1]


class Dilbert(StripSiteBase):
    comicname = "Dilbert"
    baseurl = "http://www.dilbert.com/"

    @classmethod
    def mkFromResponse(cls, resp):
        tree = BeautifulSoup(resp.get_data())

        div = tree.findAll("div", attrs={"class":"STR_Image"})
        if len(div) != 1:
            raise ParsingError("%s: Unexpected number of STR_Calendar <div>s" % (cls.__name__))
        div = div[0]
        imgs = div.findChildren("img")
        if len(imgs) != 1:
            raise ParsingError("%s: Unexpected number of comic <img>s found" % (cls.__name__))
        elif "coming_soon" in imgs[0]["src"]:
            raise StripError("%s: No strip image in %s" % (cls.__name__, resp.geturl()))
        imgurl = cls._absolute_url(resp.geturl(), img=imgs[0])

        div = tree.findAll("div", attrs={"class":"STR_Calendar"})
        if len(div) != 1:
            raise ParsingError("%s: unexpected number of STR_Calendar <div>s" % (cls.__name__))
        div = div[0]

        nexts = div.findChildren("a", attrs={"class":"STR_Next PNG_Fix"})
        if len(nexts) == 0:
            next = None
        elif len(nexts) == 1:
            next = cls._absolute_url(resp.geturl(), anchor=nexts[0])
        else:
            raise ParsingError("%s: unexpected number of STR_Next PNG_Fix <a>s" % (cls.__name__))

        prevs = div.findChildren("a", attrs={"class":"STR_Prev PNG_Fix"})
        if len(prevs) == 0:
            prev = None
        elif len(prevs) == 1:
            prev = cls._absolute_url(resp.geturl(), anchor=prevs[0])
        else:
            raise ParsingError("%s: unexpected number of STR_Prev PNG_Fix <a>s" % (cls.__name__))

        return cls(resp.geturl(), imgurl, prev, next)


comics = [Dilbert, XKCD, IncidentalComics]


class SafeTokenizer(object):
    def __init__(self, line, sep):
        self._tokens = line.split(sep)

    def __getitem__(self, idx):
        if idx < 0:
            raise ValueError("Only positive indices allowed")
        try:
            return self._tokens[idx]
        except IndexError:
            return None


# { absolute_url => StripSiteBase object }

class ComicCrawler(dict):
    """Crawler for comic strip sites.

    The comic to crawl is set by the `stripsite` and it's `baseurl` attribute.
    """

    useragent =  "Mozilla/4.0 (compatible; MSIE 8.0; Windows NT 6.1; WOW64; Trident/4.0; SLCC2; .NET CLR 2.0.50727; .NET CLR 3.5.30729; .NET CLR 3.0.30729; Media Center PC 6.0; InfoPath.3; AskTB5.6)"
    basedir = os.path.join(os.environ["HOME"], ".cache", "comiccrawler")
    indexname = "index.tsv"

    def __init__(self, stripsite):
        self.stripsite = stripsite

        self.browser = mechanize.Browser()
        self.browser.addheaders = [("User-Agent", self.useragent)]

        self._current_url = self.stripsite.baseurl

    def __getitem__(self, url):
        if not self.has_key(url):
            self.update_strip(url)
        return super(ComicCrawler, self).__getitem__(url)

    def __str__(self):
        return "<ComicCrawler for %s %d entries>" % (self.stripsite.__name__, len(self))

    def update_strip(self, url):
        logging.info("Loading {0}".format(url))
        resp = self.browser.open(url)
        strip = self.stripsite.mkFromResponse(resp)
        self._add_strip(strip)

    def go(self, direction, reload=True):
        """Move to the next/prev strip."""
        if direction == "next":
            site = self.get(1, reload)
        elif direction == "prev":
            site = self.get(-1, reload)
        else:
            raise ValueError("direction must be 'prev' or 'next'")
        self._current_url = site.url

    def get(self, dist=0, reload=True):
        """Get a strip `dist` clicks away from current strip.

        If `reload` is ``True`` refetch the intermediate sites if necessary.
        """
        buf = self[self._current_url]
        while dist != 0:
            if dist > 0:
                if buf.next is TerminusSite:
                    logging.debug("next == TerminusSite -> reloading")
                    self.update_strip(buf.url)
                    buf = self[buf.url]
                if buf.next is None:
                    raise StripError("No next site")

                # Prevent auto-reload of strip site.
                if not reload and not self.has_key(buf.next):
                    raise StripError("Next strip not loaded")

                buf = self[buf.next]
                dist -= 1

            elif dist < 0:
                if buf.prev is TerminusSite:
                    logging.debug("next == TerminusSite -> reloading")
                    self.update_strip(buf.url)
                    buf = self[buf.url]
                if buf.prev is None:
                    raise StripError("No prev site")

                # Prevent auto-reload of strip site.
                if not reload and not self.has_key(buf.prev):
                    raise StripError("Previous strip not loaded")

                buf = self[buf.prev]
                dist += 1
        return buf

    def _add_strip(self, strip):
        self[strip.url] = strip

    def get_image(self, strip):
        # Prepare save directory
        if not os.path.exists(self.savedir):
            logging.info("Creating cache directory {0}".format(self.savedir))
            os.makedirs(self.savedir)
        elif not os.path.isdir(self.savedir):
            raise IOError("Output directory '%s' is not a directory" % self.savedir)

        target = os.path.join(self.savedir, strip.savename)

        if not os.path.exists(target):
            logging.info("Downloading {0} to {1}".format(strip.img, target))
            image = urllib.URLopener()
            image.retrieve(strip.img, target)
        else:
            logging.debug("Image for url {0} already downloaded at {1}".format(strip.img, target))
        return target

    @property
    def savedir(self):
        return os.path.join(self.basedir, self.stripsite.comicname)

    @property
    def indexpath(self):
        return os.path.join(self.savedir, self.indexname)

    def load_index(self, infile=None, sep="\t"):
        if infile is None:
            infile = self.indexpath
        with open(infile, "r") as f:
            # data = [line.strip().split(sep) for line in f]
            data = [SafeTokenizer(line.strip(), sep) for line in f]
        strip = self.stripsite(data[0][0], data[0][1], data[1][0], TerminusSite, data[0][2])
        self._add_strip(strip)
        self._current_url = data[0][0]
        for i, toks in enumerate(data[1:-1]):
            i += 1
            strip = self.stripsite(toks[0], toks[1], data[i+1][0], data[i-1][0], toks[2])
            self._add_strip(strip)
        strip = self.stripsite(data[-1][0], data[-1][1], TerminusSite, data[-2][0], data[-1][2])
        self._add_strip(strip)

    def dump_index(self, outfile=None, sep="\t"):
        if outfile is None:
            outfile = self.indexpath
        strip = self[self._current_url]

        while isinstance(strip.next, basestring) and self.has_key(strip.next):
            strip = self[strip.next]

        with open(outfile, "w") as f:
            while True:
                f.write(strip.url)
                f.write(sep)
                f.write(strip.img)
                if strip.title:
                    f.write(sep)
                    f.write(strip.title)
                f.write("\n")
                if strip.prev is None or not self.has_key(strip.prev):
                    break
                strip = self[strip.prev]


# if __name__ == "__main__":
    # br = mechanize.Browser()
    # resp = br.open(Dilbert.baseurl)
    # ds = Dilbert.mkFromResponse(resp)

