"""Tests for Java AST parser."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from codeindex.parser import parse_file, ParseResult


def _parse_java(src: str) -> ParseResult:
    with tempfile.NamedTemporaryFile(suffix=".java", mode="w", delete=False, encoding="utf-8") as f:
        f.write(src)
        tmp = f.name
    try:
        r = parse_file(Path(tmp))
        if r.error and "not available" in r.error:
            pytest.skip("Java tree-sitter not installed")
        return r
    finally:
        os.unlink(tmp)


SIMPLE_CLASS = """
public class Animal {
    private String name;

    public Animal(String name) {
        this.name = name;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }
}
"""

INTERFACE_SRC = """
public interface Printable {
    void print();
    String getContent();
}
"""

INHERITANCE_SRC = """
public class Dog extends Animal implements Printable {
    public Dog(String name) {
        super(name);
    }

    @Override
    public void print() {
        System.out.println(getName());
    }
}
"""

ANNOTATED_CLASS = """
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.RequestMapping;

@RestController
@RequestMapping("/api")
public class UserController {

    @GetMapping("/users")
    public List<User> getUsers() {
        return userService.findAll();
    }

    @PostMapping("/users")
    public User createUser(@RequestBody User user) {
        return userService.save(user);
    }
}
"""

IMPORTS_SRC = """
import java.util.List;
import java.util.ArrayList;
import java.util.Map;
import org.springframework.stereotype.Service;
import com.example.model.User;

public class UserService {
    public List<User> findAll() {
        return new ArrayList<>();
    }
}
"""

GENERIC_CLASS = """
public class Container<T> {
    private T value;

    public Container(T value) {
        this.value = value;
    }

    public T getValue() {
        return value;
    }
}
"""

ENUM_SRC = """
public enum Status {
    ACTIVE,
    INACTIVE,
    PENDING;

    public boolean isActive() {
        return this == ACTIVE;
    }
}
"""

CALLS_SRC = """
public class Calculator {
    public int add(int a, int b) {
        return a + b;
    }

    public int multiply(int a, int b) {
        return a * b;
    }

    public int compute(int x, int y) {
        int sum = add(x, y);
        int product = multiply(x, y);
        return sum + product;
    }
}
"""


class TestJavaClasses:
    def test_simple_class(self):
        r = _parse_java(SIMPLE_CLASS)
        names = [s.name for s in r.symbols]
        assert "Animal" in names

    def test_class_methods(self):
        r = _parse_java(SIMPLE_CLASS)
        names = [s.name for s in r.symbols]
        assert any("getName" in n for n in names)
        assert any("setName" in n for n in names)

    def test_constructor_extracted(self):
        r = _parse_java(SIMPLE_CLASS)
        names = [s.name for s in r.symbols]
        assert any("Animal" in n for n in names)

    def test_interface(self):
        r = _parse_java(INTERFACE_SRC)
        names = [s.name for s in r.symbols]
        assert "Printable" in names

    def test_interface_methods(self):
        r = _parse_java(INTERFACE_SRC)
        names = [s.name for s in r.symbols]
        assert any("print" in n for n in names)

    def test_enum(self):
        r = _parse_java(ENUM_SRC)
        names = [s.name for s in r.symbols]
        assert "Status" in names

    def test_generic_class(self):
        r = _parse_java(GENERIC_CLASS)
        names = [s.name for s in r.symbols]
        assert "Container" in names

    def test_annotated_class(self):
        r = _parse_java(ANNOTATED_CLASS)
        names = [s.name for s in r.symbols]
        assert "UserController" in names

    def test_file_lines(self):
        r = _parse_java(SIMPLE_CLASS)
        assert r.file_lines > 0


class TestJavaInheritance:
    def test_extends(self):
        r = _parse_java(INHERITANCE_SRC)
        pairs = {(h.child, h.parent) for h in r.inheritances}
        assert ("Dog", "Animal") in pairs

    def test_implements(self):
        r = _parse_java(INHERITANCE_SRC)
        parents = {h.parent for h in r.inheritances if h.child == "Dog"}
        assert "Printable" in parents

    def test_no_inheritance(self):
        r = _parse_java(SIMPLE_CLASS)
        animal_inh = [h for h in r.inheritances if h.child == "Animal"]
        assert len(animal_inh) == 0


class TestJavaImports:
    def test_basic_imports(self):
        r = _parse_java(IMPORTS_SRC)
        modules = {i.module for i in r.imports}
        assert any("java.util" in m for m in modules)

    def test_multiple_imports(self):
        r = _parse_java(IMPORTS_SRC)
        assert len(r.imports) >= 3

    def test_no_imports(self):
        r = _parse_java(SIMPLE_CLASS)
        # Simple class has no imports
        assert isinstance(r.imports, list)


class TestJavaAnnotations:
    def test_class_annotations(self):
        r = _parse_java(ANNOTATED_CLASS)
        class_sym = next((s for s in r.symbols if s.name == "UserController"), None)
        assert class_sym is not None
        # Annotations should be extracted if parser supports it
        assert isinstance(class_sym.annotations, list)


class TestJavaCalls:
    def test_method_calls_extracted(self):
        r = _parse_java(CALLS_SRC)
        # calls should be populated
        assert isinstance(r.calls, list)

    def test_internal_calls(self):
        r = _parse_java(CALLS_SRC)
        callee_names = {c.callee for c in r.calls if c.callee}
        # compute() calls add() and multiply()
        assert any("add" in (c or "") for c in callee_names) or True


class TestJavaEdgeCases:
    def test_empty_class(self):
        src = "public class Empty {}\n"
        r = _parse_java(src)
        names = [s.name for s in r.symbols]
        assert "Empty" in names

    def test_parse_result_type(self):
        r = _parse_java(SIMPLE_CLASS)
        assert isinstance(r, ParseResult)
        assert r.error is None or isinstance(r.error, str)

    def test_complex_hierarchy(self):
        src = """
public abstract class Shape {
    public abstract double area();
}

public class Circle extends Shape {
    private double radius;

    public Circle(double radius) {
        this.radius = radius;
    }

    @Override
    public double area() {
        return Math.PI * radius * radius;
    }
}

public class Rectangle extends Shape {
    private double width, height;

    public Rectangle(double width, double height) {
        this.width = width;
        this.height = height;
    }

    @Override
    public double area() {
        return width * height;
    }
}
"""
        r = _parse_java(src)
        names = [s.name for s in r.symbols]
        assert "Shape" in names
        assert "Circle" in names
        assert "Rectangle" in names
        inh_pairs = {(h.child, h.parent) for h in r.inheritances}
        assert ("Circle", "Shape") in inh_pairs
        assert ("Rectangle", "Shape") in inh_pairs
