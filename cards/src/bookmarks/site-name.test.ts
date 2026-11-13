import { describe, expect, it } from 'vitest'

import { siteNameBeside } from './site-name.ts'

const HOST = 'blog.example.com'

describe('siteNameBeside', () => {
  it.each([
    'Robin Sloan',
    'GitHub',
    'The Example Blog',
    'Wikipedia, the free encyclopedia',
    'U.S. News',
    'Dr. Who Fans',
    'Version 2.0 notes',
    'Example — est. 1999',
  ])('shows an ordinary name: %s', (name) => {
    expect(siteNameBeside(name, HOST)).toBe(name)
  })

  it.each([
    ['the row’s own host', 'blog.example.com'],
    ['its own host in capitals', 'Blog.Example.COM'],
    ['its own host with www.', 'www.blog.example.com'],
    ['nothing but spaces', '   '],
  ])('shows nothing for %s: it adds nothing', (_name, siteName) => {
    expect(siteNameBeside(siteName, HOST)).toBeUndefined()
  })

  it.each([
    ['The Example Blog (blog.example.com)'],
    ['example.com — the blog'],
    ['Blog at Example.com'],
    ['https://blog.example.com/'],
    ['www.example.com news'],
  ])('shows a name that mentions its own host or a parent of it: %s', (siteName) => {
    expect(siteNameBeside(siteName, HOST)).toBe(siteName)
  })

  it.each([
    ['a bare host', 'paypal.com'],
    ['a host with a slash', 'paypal.com/'],
    ['a URL', 'https://paypal.com/'],
    ['a URL with a path', 'https://paypal.com/signin?x=1'],
    ['a host among other words', 'paypal.com – Secure login'],
    ['a host at the end', 'Secure login | paypal.com'],
    ['a host in brackets', 'PayPal (paypal.com)'],
    ['a subdomain of another site', 'accounts.google.com'],
    ['a host with www.', 'www.paypal.com'],
    ['a host in capitals', 'PAYPAL.COM'],
    ['a host that only shares a suffix with this one', 'evil-example.com'],
    ['a child of this host, which is another host', 'login.blog.example.com'],
    ['its own host beside another', 'blog.example.com and paypal.com'],
    ['an internationalised host', 'bücher.example'],
    ['a host written with a full-width dot', 'paypal．com'],
    ['a host written with an ideographic full stop', 'paypal。com'],
    ['a host with an invisible character in it', 'paypal​.com'],
    ['an address', '192.168.1.1'],
    ['an address among words', 'Router at 10.0.0.1'],
    ['a URL with no host worth the name', 'https://localhost/'],
    ['a name shaped like a host', 'Node.js'],
  ])('shows nothing for %s', (_name, siteName) => {
    expect(siteNameBeside(siteName, HOST)).toBeUndefined()
  })

  it('shows nothing when there is no name, or no host to show it beside', () => {
    expect(siteNameBeside(undefined, HOST)).toBeUndefined()
    expect(siteNameBeside('Robin Sloan', undefined)).toBeUndefined()
  })

  it('compares against the host without its www.', () => {
    expect(siteNameBeside('example.org', 'www.example.org')).toBeUndefined()
    expect(siteNameBeside('Read it on example.org', 'www.example.org')).toBe('Read it on example.org')
  })

  it('returns the name as it was given, not as it was read', () => {
    expect(siteNameBeside('Ｅxample Blog', HOST)).toBe('Ｅxample Blog')
  })
})
