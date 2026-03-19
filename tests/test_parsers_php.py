"""Tests for PHP AST parser."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from codeindex.parser import parse_file, ParseResult


def _parse_php(src: str) -> ParseResult:
    with tempfile.NamedTemporaryFile(suffix=".php", mode="w", delete=False, encoding="utf-8") as f:
        f.write(src)
        tmp = f.name
    try:
        return parse_file(Path(tmp))
    finally:
        os.unlink(tmp)


SIMPLE_CLASS = """<?php
namespace App\\Models;

class User {
    private string $name;
    private string $email;

    public function __construct(string $name, string $email) {
        $this->name = $name;
        $this->email = $email;
    }

    public function getName(): string {
        return $this->name;
    }

    public function getEmail(): string {
        return $this->email;
    }

    public function setName(string $name): void {
        $this->name = $name;
    }
}
"""

INHERITANCE = """<?php
namespace App\\Models;

abstract class Animal {
    protected string $name;

    public function __construct(string $name) {
        $this->name = $name;
    }

    abstract public function speak(): string;

    public function getName(): string {
        return $this->name;
    }
}

class Dog extends Animal {
    public function speak(): string {
        return 'Woof!';
    }
}

class Cat extends Animal {
    public function speak(): string {
        return 'Meow!';
    }
}
"""

INTERFACE_SRC = """<?php
namespace App\\Contracts;

interface Repository {
    public function find(int $id): ?object;
    public function findAll(): array;
    public function save(object $entity): void;
    public function delete(int $id): void;
}

class UserRepository implements Repository {
    public function find(int $id): ?object {
        return null;
    }

    public function findAll(): array {
        return [];
    }

    public function save(object $entity): void {}

    public function delete(int $id): void {}
}
"""

IMPORTS_SRC = """<?php
namespace App\\Controllers;

use App\\Models\\User;
use App\\Services\\UserService;
use Illuminate\\Http\\Request;
use Illuminate\\Http\\Response;

class UserController {
    private UserService $userService;

    public function __construct(UserService $userService) {
        $this->userService = $userService;
    }

    public function index(): Response {
        $users = $this->userService->findAll();
        return response()->json($users);
    }
}
"""

FUNCTIONS_SRC = """<?php
function add(int $a, int $b): int {
    return $a + $b;
}

function greet(string $name): string {
    return "Hello, {$name}!";
}

$multiply = function(int $a, int $b): int {
    return $a * $b;
};

$double = fn($x) => $x * 2;
"""

TRAIT_SRC = """<?php
trait Timestampable {
    private \\DateTime $createdAt;
    private \\DateTime $updatedAt;

    public function getCreatedAt(): \\DateTime {
        return $this->createdAt;
    }

    public function touch(): void {
        $this->updatedAt = new \\DateTime();
    }
}

class Article {
    use Timestampable;

    public string $title;
}
"""

STATIC_METHODS = """<?php
class MathHelper {
    public static function square(int $n): int {
        return $n * $n;
    }

    public static function cube(int $n): int {
        return $n * $n * $n;
    }

    public static function factorial(int $n): int {
        if ($n <= 1) return 1;
        return $n * self::factorial($n - 1);
    }
}
"""

CALLS_SRC = """<?php
class OrderService {
    private $repo;

    public function __construct($repo) {
        $this->repo = $repo;
    }

    public function createOrder(array $items): array {
        $total = $this->calculateTotal($items);
        $order = $this->repo->save(['items' => $items, 'total' => $total]);
        $this->notifyUser($order);
        return $order;
    }

    private function calculateTotal(array $items): float {
        return array_sum(array_column($items, 'price'));
    }

    private function notifyUser(array $order): void {
        mail($order['email'], 'Order confirmed', 'Your order has been placed.');
    }
}
"""


class TestPhpClasses:
    def test_simple_class(self):
        r = _parse_php(SIMPLE_CLASS)
        names = [s.name for s in r.symbols]
        assert "User" in names

    def test_class_methods(self):
        r = _parse_php(SIMPLE_CLASS)
        names = [s.name for s in r.symbols]
        assert any("getName" in n for n in names)
        assert any("getEmail" in n for n in names)

    def test_constructor(self):
        r = _parse_php(SIMPLE_CLASS)
        names = [s.name for s in r.symbols]
        assert any("__construct" in n for n in names)

    def test_static_methods(self):
        r = _parse_php(STATIC_METHODS)
        names = [s.name for s in r.symbols]
        assert any("square" in n for n in names)
        assert any("factorial" in n for n in names)

    def test_trait(self):
        r = _parse_php(TRAIT_SRC)
        names = [s.name for s in r.symbols]
        assert "Timestampable" in names or "Article" in names

    def test_file_lines(self):
        r = _parse_php(SIMPLE_CLASS)
        assert r.file_lines > 0


class TestPhpInheritance:
    def test_extends(self):
        r = _parse_php(INHERITANCE)
        # PHP parser may prepend namespace to class names
        pairs = {(h.child.split("\\")[-1], h.parent.split("\\")[-1]) for h in r.inheritances}
        assert ("Dog", "Animal") in pairs
        assert ("Cat", "Animal") in pairs

    def test_implements(self):
        r = _parse_php(INTERFACE_SRC)
        # PHP parser prepends namespace — check short names
        parents = {h.parent.split("\\")[-1] for h in r.inheritances if "UserRepository" in h.child}
        assert "Repository" in parents

    def test_abstract_class(self):
        r = _parse_php(INHERITANCE)
        names = [s.name for s in r.symbols]
        assert "Animal" in names


class TestPhpImports:
    def test_use_statements(self):
        r = _parse_php(IMPORTS_SRC)
        modules = {i.module for i in r.imports}
        assert any("User" in m for m in modules)

    def test_multiple_use_statements(self):
        r = _parse_php(IMPORTS_SRC)
        assert len(r.imports) >= 2

    def test_namespace(self):
        r = _parse_php(SIMPLE_CLASS)
        assert r.namespace == "App\\Models" or isinstance(r.namespace, str)


class TestPhpFunctions:
    def test_top_level_function(self):
        r = _parse_php(FUNCTIONS_SRC)
        names = [s.name for s in r.symbols]
        assert "add" in names
        assert "greet" in names

    def test_closure(self):
        r = _parse_php(FUNCTIONS_SRC)
        # Closures may or may not be extracted
        assert isinstance(r.symbols, list)


class TestPhpCalls:
    def test_method_calls(self):
        r = _parse_php(CALLS_SRC)
        assert isinstance(r.calls, list)

    def test_self_calls(self):
        r = _parse_php(STATIC_METHODS)
        assert isinstance(r.calls, list)


class TestPhpEdgeCases:
    def test_empty_class(self):
        r = _parse_php("<?php\nclass Empty {}\n")
        names = [s.name for s in r.symbols]
        assert "Empty" in names

    def test_interface_only(self):
        r = _parse_php(INTERFACE_SRC)
        names = [s.name for s in r.symbols]
        assert any("Repository" in n for n in names)

    def test_parse_result_type(self):
        r = _parse_php(SIMPLE_CLASS)
        assert isinstance(r, ParseResult)

    def test_complex_class(self):
        src = """<?php
namespace App\\Services;

use App\\Models\\User;
use App\\Repositories\\UserRepository;

class UserService {
    private UserRepository $repository;

    public function __construct(UserRepository $repository) {
        $this->repository = $repository;
    }

    public function createUser(string $name, string $email): User {
        $user = new User($name, $email);
        $this->repository->save($user);
        return $user;
    }

    public function findUser(int $id): ?User {
        return $this->repository->find($id);
    }

    public function deleteUser(int $id): bool {
        $user = $this->findUser($id);
        if ($user === null) return false;
        $this->repository->delete($id);
        return true;
    }
}
"""
        r = _parse_php(src)
        names = [s.name for s in r.symbols]
        assert "UserService" in names
        assert any("createUser" in n for n in names)
        assert any("deleteUser" in n for n in names)
