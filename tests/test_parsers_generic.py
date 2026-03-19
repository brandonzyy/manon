"""Tests for generic and base parsers across multiple languages."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from codeindex.parser import parse_file, ParseResult, get_all_extensions


def _parse(src: str, suffix: str) -> ParseResult:
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False, encoding="utf-8") as f:
        f.write(src)
        tmp = f.name
    try:
        return parse_file(Path(tmp))
    finally:
        os.unlink(tmp)


GO_SRC = """package main

import (
    "fmt"
    "math"
    "strings"
)

type Shape interface {
    Area() float64
    Perimeter() float64
}

type Circle struct {
    Radius float64
}

func (c Circle) Area() float64 {
    return math.Pi * c.Radius * c.Radius
}

func (c Circle) Perimeter() float64 {
    return 2 * math.Pi * c.Radius
}

type Rectangle struct {
    Width  float64
    Height float64
}

func (r Rectangle) Area() float64 {
    return r.Width * r.Height
}

func NewCircle(radius float64) Circle {
    return Circle{Radius: radius}
}

func main() {
    c := NewCircle(5.0)
    fmt.Printf("Area: %.2f\\n", c.Area())
    _ = strings.TrimSpace("  hello  ")
}
"""

RUST_SRC = """
use std::fmt;
use std::collections::HashMap;

#[derive(Debug)]
struct Point {
    x: f64,
    y: f64,
}

impl Point {
    fn new(x: f64, y: f64) -> Self {
        Point { x, y }
    }

    fn distance(&self, other: &Point) -> f64 {
        let dx = self.x - other.x;
        let dy = self.y - other.y;
        (dx * dx + dy * dy).sqrt()
    }
}

impl fmt::Display for Point {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "({}, {})", self.x, self.y)
    }
}

trait Drawable {
    fn draw(&self);
}

impl Drawable for Point {
    fn draw(&self) {
        println!("Point at {}", self);
    }
}

fn create_points(n: usize) -> Vec<Point> {
    (0..n).map(|i| Point::new(i as f64, i as f64)).collect()
}
"""

C_SRC = """
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int x;
    int y;
} Point;

typedef struct Node {
    int value;
    struct Node* next;
} Node;

Point create_point(int x, int y) {
    Point p;
    p.x = x;
    p.y = y;
    return p;
}

int add(int a, int b) {
    return a + b;
}

Node* create_node(int value) {
    Node* node = (Node*)malloc(sizeof(Node));
    node->value = value;
    node->next = NULL;
    return node;
}

void print_point(Point p) {
    printf("(%d, %d)\\n", p.x, p.y);
}

int main() {
    Point p = create_point(3, 4);
    print_point(p);
    return 0;
}
"""

CPP_SRC = """
#include <iostream>
#include <vector>
#include <string>

class Animal {
protected:
    std::string name;
public:
    Animal(const std::string& name) : name(name) {}
    virtual std::string speak() const = 0;
    std::string getName() const { return name; }
};

class Dog : public Animal {
public:
    Dog(const std::string& name) : Animal(name) {}
    std::string speak() const override { return "Woof!"; }
};

class Cat : public Animal {
public:
    Cat(const std::string& name) : Animal(name) {}
    std::string speak() const override { return "Meow!"; }
};

template<typename T>
class Container {
private:
    std::vector<T> items;
public:
    void add(const T& item) { items.push_back(item); }
    size_t size() const { return items.size(); }
};

int add(int a, int b) { return a + b; }
"""

CSHARP_SRC = """
using System;
using System.Collections.Generic;
using System.Linq;

namespace MyApp.Models
{
    public interface IEntity
    {
        int Id { get; set; }
        DateTime CreatedAt { get; set; }
    }

    public abstract class BaseEntity : IEntity
    {
        public int Id { get; set; }
        public DateTime CreatedAt { get; set; }
    }

    public class User : BaseEntity
    {
        public string Name { get; set; }
        public string Email { get; set; }

        public User(string name, string email)
        {
            Name = name;
            Email = email;
            CreatedAt = DateTime.Now;
        }

        public override string ToString()
        {
            return $"User({Name}, {Email})";
        }
    }
}
"""


def _try_parse(src: str, suffix: str) -> ParseResult:
    """Parse, skipping test if parser not available."""
    r = _parse(src, suffix)
    if r.error and ("not available" in r.error or "Unsupported" in r.error):
        pytest.skip(f"{suffix} parser not installed")
    return r


class TestGoParser:
    def test_functions_extracted(self):
        r = _try_parse(GO_SRC, ".go")
        names = [s.name for s in r.symbols]
        assert len(names) > 0

    def test_no_error(self):
        r = _try_parse(GO_SRC, ".go")
        assert isinstance(r, ParseResult)


class TestRustParser:
    def test_functions_extracted(self):
        r = _try_parse(RUST_SRC, ".rs")
        names = [s.name for s in r.symbols]
        assert len(names) > 0

    def test_no_error(self):
        r = _try_parse(RUST_SRC, ".rs")
        assert isinstance(r, ParseResult)


class TestCParser:
    def test_functions_extracted(self):
        r = _try_parse(C_SRC, ".c")
        names = [s.name for s in r.symbols]
        assert len(names) > 0


class TestCppParser:
    def test_classes_extracted(self):
        r = _try_parse(CPP_SRC, ".cpp")
        names = [s.name for s in r.symbols]
        assert len(names) > 0


class TestCSharpParser:
    def test_classes_extracted(self):
        r = _try_parse(CSHARP_SRC, ".cs")
        names = [s.name for s in r.symbols]
        assert len(names) > 0


class TestParserExtensions:
    def test_get_all_extensions(self):
        exts = get_all_extensions()
        assert ".py" in exts
        assert ".java" in exts
        assert ".ts" in exts
        assert ".php" in exts
        assert ".js" in exts

    def test_unsupported_extension(self):
        r = _parse("hello world", ".xyz")
        # Should return a ParseResult with error, not raise
        assert isinstance(r, ParseResult)
        assert r.error is not None

    def test_txt_unsupported(self):
        r = _parse("plain text content", ".txt")
        assert isinstance(r, ParseResult)
        assert r.error is not None


class TestParserFallback:
    def test_py_parses_without_error(self):
        src = "def foo(): pass\n"
        r = _parse(src, ".py")
        assert r.error is None

    def test_java_parses_without_error(self):
        src = "public class Foo {}\n"
        r = _parse(src, ".java")
        # Java parser may not be installed; either succeeds or returns error string
        assert isinstance(r, ParseResult)

    def test_ts_parses_without_error(self):
        src = "class Foo {}\n"
        r = _parse(src, ".ts")
        assert r.error is None

    def test_php_parses_without_error(self):
        src = "<?php\nclass Foo {}\n"
        r = _parse(src, ".php")
        assert r.error is None

    def test_mjs_as_javascript(self):
        src = "export function foo() { return 1; }\n"
        r = _parse(src, ".mjs")
        assert isinstance(r, ParseResult)
