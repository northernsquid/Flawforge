import io
import socket
import unittest
from unittest import mock

import app as appmod

PUBLIC = "93.184.215.14"


def gai(*ips):
    out = []
    for ip in ips:
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        addr = (ip, 80, 0, 0) if fam == socket.AF_INET6 else (ip, 80)
        out.append((fam, socket.SOCK_STREAM, 6, "", addr))
    return out


class FakeSock:
    def __init__(self, response):
        self.response = response
        self.sent = b""

    def sendall(self, data):
        self.sent += data

    def makefile(self, mode, *a, **k):
        return io.BytesIO(self.response)

    def close(self):
        pass


OK = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"
REDIRECT = b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/\r\nContent-Length: 0\r\n\r\n"


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.client = appmod.app.test_client()

    def get(self, url):
        return self.client.get("/fetch", query_string={"url": url})

    def test_blocked_ranges(self):
        blocked = [
            ("127.0.0.1",), ("10.0.0.1",), ("172.16.0.1",), ("192.168.1.1",),
            ("169.254.169.254",), ("0.0.0.0",), ("::1",), ("::ffff:127.0.0.1",),
            ("fe80::1",), ("fe80::1%eth0",), (PUBLIC, "10.0.0.1"), ("224.0.0.1",),
            # Embedded non-global IPv4 that is_global misses (NAT64, compat, SIIT, site-local)
            ("64:ff9b::7f00:1",), ("64:ff9b::a9fe:a9fe",), ("64:ff9b:1::1",),
            ("::7f00:1",), ("fec0::1",), ("::ffff:0:7f00:1",), ("100.64.0.1",),
            ("198.18.0.1",), ("240.0.0.1",),
        ]
        for ips in blocked:
            with self.subTest(ips=ips), \
                    mock.patch("socket.getaddrinfo", return_value=gai(*ips)), \
                    mock.patch("socket.create_connection") as cc:
                r = self.get("http://example.com/")
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json(), {"error": "invalid host"})
                cc.assert_not_called()

    def test_empty_resolution_rejected(self):
        with mock.patch("socket.getaddrinfo", return_value=[]):
            r = self.get("http://example.com/")
        self.assertEqual(r.get_json(), {"error": "invalid host"})

    def test_resolution_error_rejected(self):
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror):
            r = self.get("http://example.com/")
        self.assertEqual(r.get_json(), {"error": "invalid host"})

    def test_no_hostname(self):
        for url in ("http://", "http:///path", "https://:80/"):
            with self.subTest(url=url), mock.patch("socket.getaddrinfo") as g:
                r = self.get(url)
                self.assertEqual(r.status_code, 400)
                g.assert_not_called()

    def test_port_zero(self):
        with mock.patch("socket.getaddrinfo") as g:
            r = self.get("http://example.com:0/")
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.get_json(), {"error": "invalid host"})
            g.assert_not_called()

    def test_bad_scheme(self):
        r = self.get("file:///etc/passwd")
        self.assertEqual(r.get_json(), {"error": "invalid scheme"})

    def test_rebind_resolves_once_and_connects_to_checked_ip(self):
        g = mock.Mock(side_effect=[gai(PUBLIC)] + [gai("127.0.0.1")] * 5)
        sock = FakeSock(OK)
        with mock.patch("socket.getaddrinfo", g), \
                mock.patch("socket.create_connection", return_value=sock) as cc:
            r = self.get("http://rebind.example:8080/a?b=c")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"url": "http://rebind.example:8080/a?b=c", "body": "hello"})
        self.assertEqual(g.call_count, 1)
        cc.assert_called_once_with((PUBLIC, 8080), timeout=5)
        self.assertTrue(sock.sent.startswith(b"GET /a?b=c HTTP/1.1\r\n"))
        self.assertIn(b"Host: rebind.example:8080\r\n", sock.sent)

    def test_redirect_refused(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status), \
                    mock.patch("socket.getaddrinfo", return_value=gai(PUBLIC)), \
                    mock.patch("socket.create_connection", return_value=FakeSock(REDIRECT.replace(b"302", f"{status}".encode()))):
                r = self.get("http://example.com/")
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json(), {"error": "redirect refused"})

    def test_https_wraps_with_original_hostname(self):
        raw = object()
        ctx = mock.Mock()
        ctx.wrap_socket.return_value = FakeSock(OK)
        with mock.patch("socket.getaddrinfo", return_value=gai(PUBLIC)), \
                mock.patch("socket.create_connection", return_value=raw) as cc, \
                mock.patch("ssl.create_default_context", return_value=ctx):
            r = self.get("https://secure.example/x")
        self.assertEqual(r.status_code, 200)
        cc.assert_called_once_with((PUBLIC, 443), timeout=5)
        ctx.wrap_socket.assert_called_once_with(raw, server_hostname="secure.example")

    def test_connect_failure(self):
        with mock.patch("socket.getaddrinfo", return_value=gai(PUBLIC)), \
                mock.patch("socket.create_connection", side_effect=OSError):
            r = self.get("http://example.com/")
        self.assertEqual(r.get_json(), {"error": "fetch failed"})

    def test_positive_controls(self):
        # These should succeed
        for ip in ("::ffff:8.8.8.8", "2606:4700::1", PUBLIC):
            with self.subTest(ip=ip), \
                    mock.patch("socket.getaddrinfo", return_value=gai(ip)), \
                    mock.patch("socket.create_connection", return_value=FakeSock(OK)) as cc:
                r = self.get("http://example.com/")
                self.assertEqual(r.status_code, 200)
                self.assertIn("url", r.get_json())
                cc.assert_called_once()


if __name__ == "__main__":
    unittest.main()
