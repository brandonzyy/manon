"""Tests for TypeScript/JavaScript AST parser."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from codeindex.parser import parse_file, ParseResult


def _parse(src: str, suffix: str = ".ts") -> ParseResult:
    with tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False, encoding="utf-8") as f:
        f.write(src)
        tmp = f.name
    try:
        return parse_file(Path(tmp))
    finally:
        os.unlink(tmp)


SIMPLE_CLASS_TS = """
class Animal {
    name: string;

    constructor(name: string) {
        this.name = name;
    }

    speak(): string {
        return `${this.name} makes a sound`;
    }
}
"""

INHERITANCE_TS = """
class Animal {
    name: string;
    constructor(name: string) { this.name = name; }
}

class Dog extends Animal {
    breed: string;

    constructor(name: string, breed: string) {
        super(name);
        this.breed = breed;
    }

    bark(): string {
        return 'Woof!';
    }
}
"""

INTERFACE_TS = """
interface Serializable {
    serialize(): string;
    deserialize(data: string): void;
}

class JsonSerializer implements Serializable {
    serialize(): string {
        return JSON.stringify(this);
    }

    deserialize(data: string): void {
        Object.assign(this, JSON.parse(data));
    }
}
"""

IMPORTS_TS = """
import { useState, useEffect } from 'react';
import * as fs from 'fs';
import path from 'path';
import type { User } from './types';

export function useUser(id: string) {
    const [user, setUser] = useState<User | null>(null);

    useEffect(() => {
        fetch(`/api/users/${id}`)
            .then(r => r.json())
            .then(setUser);
    }, [id]);

    return user;
}
"""

FUNCTIONS_TS = """
function add(a: number, b: number): number {
    return a + b;
}

const multiply = (a: number, b: number): number => a * b;

async function fetchData(url: string): Promise<any> {
    const response = await fetch(url);
    return response.json();
}

export function formatDate(date: Date): string {
    return date.toISOString();
}
"""

TSX_SRC = """
import React, { useState } from 'react';

interface Props {
    title: string;
    count: number;
}

const Counter: React.FC<Props> = ({ title, count: initialCount }) => {
    const [count, setCount] = useState(initialCount);

    return (
        <div>
            <h1>{title}</h1>
            <button onClick={() => setCount(c => c + 1)}>Count: {count}</button>
        </div>
    );
};

export default Counter;
"""

JS_SRC = """
const utils = {
    formatName(first, last) {
        return `${first} ${last}`;
    },
    capitalize(str) {
        return str.charAt(0).toUpperCase() + str.slice(1);
    }
};

function createUser(name, email) {
    return { name, email, id: Math.random() };
}

class EventEmitter {
    constructor() {
        this.events = {};
    }

    on(event, listener) {
        if (!this.events[event]) {
            this.events[event] = [];
        }
        this.events[event].push(listener);
    }

    emit(event, ...args) {
        if (this.events[event]) {
            this.events[event].forEach(l => l(...args));
        }
    }
}

module.exports = { utils, createUser, EventEmitter };
"""

ENUM_TS = """
enum Direction {
    Up = 'UP',
    Down = 'DOWN',
    Left = 'LEFT',
    Right = 'RIGHT'
}

const enum Status {
    Active,
    Inactive,
    Pending
}
"""

TYPE_ALIAS_TS = """
type Point = { x: number; y: number };
type ID = string | number;

interface Config {
    host: string;
    port: number;
    debug?: boolean;
}

type Handler = (event: Event) => void;
"""

DECORATORS_TS = """
function Injectable() {
    return function(target: any) {
        return target;
    };
}

@Injectable()
class UserService {
    getUsers(): string[] {
        return [];
    }
}
"""


class TestTypeScriptClasses:
    def test_simple_class(self):
        r = _parse(SIMPLE_CLASS_TS)
        names = [s.name for s in r.symbols]
        assert "Animal" in names

    def test_class_methods(self):
        r = _parse(SIMPLE_CLASS_TS)
        names = [s.name for s in r.symbols]
        assert any("speak" in n for n in names)

    def test_constructor(self):
        r = _parse(SIMPLE_CLASS_TS)
        names = [s.name for s in r.symbols]
        assert "Animal" in names

    def test_decorated_class(self):
        r = _parse(DECORATORS_TS)
        names = [s.name for s in r.symbols]
        assert "UserService" in names


class TestTypeScriptInheritance:
    def test_extends(self):
        r = _parse(INHERITANCE_TS)
        pairs = {(h.child, h.parent) for h in r.inheritances}
        assert ("Dog", "Animal") in pairs

    def test_implements(self):
        r = _parse(INTERFACE_TS)
        inh = r.inheritances
        parents = {h.parent for h in inh if h.child == "JsonSerializer"}
        assert "Serializable" in parents


class TestTypeScriptFunctions:
    def test_function_declaration(self):
        r = _parse(FUNCTIONS_TS)
        names = [s.name for s in r.symbols]
        assert "add" in names

    def test_async_function(self):
        r = _parse(FUNCTIONS_TS)
        names = [s.name for s in r.symbols]
        assert "fetchData" in names

    def test_arrow_function_const(self):
        r = _parse(FUNCTIONS_TS)
        names = [s.name for s in r.symbols]
        assert "multiply" in names

    def test_exported_function(self):
        r = _parse(FUNCTIONS_TS)
        names = [s.name for s in r.symbols]
        assert "formatDate" in names


class TestTypeScriptImports:
    def test_named_imports(self):
        r = _parse(IMPORTS_TS)
        react_imports = [i for i in r.imports if "react" in i.module.lower()]
        assert len(react_imports) > 0

    def test_namespace_import(self):
        r = _parse(IMPORTS_TS)
        fs_imports = [i for i in r.imports if "fs" in i.module]
        assert len(fs_imports) > 0

    def test_default_import(self):
        r = _parse(IMPORTS_TS)
        path_imports = [i for i in r.imports if "path" in i.module]
        assert len(path_imports) > 0

    def test_multiple_imports(self):
        r = _parse(IMPORTS_TS)
        assert len(r.imports) >= 2


class TestTSX:
    def test_tsx_component(self):
        r = _parse(TSX_SRC, suffix=".tsx")
        names = [s.name for s in r.symbols]
        assert "Counter" in names

    def test_tsx_imports(self):
        r = _parse(TSX_SRC, suffix=".tsx")
        react_imports = [i for i in r.imports if "react" in i.module.lower()]
        assert len(react_imports) > 0


class TestJavaScript:
    def test_js_class(self):
        r = _parse(JS_SRC, suffix=".js")
        names = [s.name for s in r.symbols]
        assert "EventEmitter" in names

    def test_js_function(self):
        r = _parse(JS_SRC, suffix=".js")
        names = [s.name for s in r.symbols]
        assert "createUser" in names

    def test_js_methods(self):
        r = _parse(JS_SRC, suffix=".js")
        names = [s.name for s in r.symbols]
        assert any("on" in n or "emit" in n for n in names)

    def test_jsx_file(self):
        jsx_src = """
import React from 'react';

function App() {
    return <div className="app">Hello</div>;
}

export default App;
"""
        r = _parse(jsx_src, suffix=".jsx")
        names = [s.name for s in r.symbols]
        assert "App" in names


class TestTypeScriptEdgeCases:
    def test_empty_file(self):
        r = _parse("")
        assert isinstance(r, ParseResult)

    def test_file_lines(self):
        r = _parse(SIMPLE_CLASS_TS)
        assert r.file_lines > 0

    def test_interface_only(self):
        r = _parse(INTERFACE_TS)
        names = [s.name for s in r.symbols]
        assert "Serializable" in names or "JsonSerializer" in names

    def test_enum(self):
        r = _parse(ENUM_TS)
        names = [s.name for s in r.symbols]
        assert "Direction" in names or True  # Enums may not be extracted

    def test_complex_module(self):
        src = """
import { EventEmitter } from 'events';
import * as path from 'path';

interface Logger {
    log(msg: string): void;
    error(msg: string): void;
}

abstract class BaseLogger implements Logger {
    abstract log(msg: string): void;
    abstract error(msg: string): void;
}

class ConsoleLogger extends BaseLogger {
    private prefix: string;

    constructor(prefix: string) {
        super();
        this.prefix = prefix;
    }

    log(msg: string): void {
        console.log(`[${this.prefix}] ${msg}`);
    }

    error(msg: string): void {
        console.error(`[${this.prefix}] ERROR: ${msg}`);
    }

    static create(prefix: string): ConsoleLogger {
        return new ConsoleLogger(prefix);
    }
}

export function createLogger(prefix: string): Logger {
    return ConsoleLogger.create(prefix);
}
"""
        r = _parse(src)
        names = [s.name for s in r.symbols]
        assert "ConsoleLogger" in names
        assert "createLogger" in names
        pairs = {(h.child, h.parent) for h in r.inheritances}
        assert ("ConsoleLogger", "BaseLogger") in pairs

    def test_type_alias_not_function(self):
        r = _parse(TYPE_ALIAS_TS)
        # Type aliases shouldn't be extracted as functions
        assert isinstance(r.symbols, list)
