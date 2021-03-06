#! /usr/bin/env python
# encoding: utf-8
# Calle Rosenquist, 2016-2017 (xbreak)

"""
Provides Python unit test support using :py:class:`waflib.Tools.waf_unit_test.utest`
task via the **pytest** feature.

The "use" dependencies are used for both update calculation and to populate
the following environment variables:

1. ``sys.path`` via the ``PYTHONPATH`` environment variable of any dependent taskgen that 
   has the feature ``py`` (i.e pure Python dependencies) or ``pyext`` (i.e. Python extensions).

2. Dynamic linker search path variable (e.g. ``LD_LIBRARY_PATH`' of any dependent taskgen with 
   non-static link_task.

To use pytest the following is needed:

1. Load ``pytest`` and the dependency ``waf_unit_test`` in configure.
2. Create a task generator with feature ``pytest`` (not ``test``) and customize behaviour with the
   following attributes:

   * ``pytest_source``: Test input files.
   * ``ut_str``: Test runner command, e.g. ``${PYTHON} -B -m unittest discover`` or
                 if nose is used: ``${NOSETESTS} --no-byte-compile ${SRC}``.
   * ``ut_shell``: Determines if ``ut_str`` is executed in a shell. Default: False.
   * ``ut_cwd``: Working directory for test runner. Defaults to directory of
                 first ``pytest_source`` file.

For example::
	
	# A python C extension that demonstrates unit test environment population
	# of PYTHONPATH and LD_LIBRARY_PATH/PATH/DYLD_LIBRARY_PATH.
	bld(name         = 'foo_ext',
		features     = 'c cshlib pyext',
		source       = 'src/foo_ext.c',
		target       = 'foo_ext')
	
	# Python package under test
	bld(name         = 'foo',
		features     = 'py',
		use          = 'foo_ext',
		source       = bld.path.ant_glob('src/foo/*.py'),
		install_from = 'src')

	# Unit test example using the built in module unittest and let that discover
	# any test cases.
	bld(name          = 'foo_test',
		features      = 'pytest',
		use           = 'foo',
		pytest_source = bld.path.ant_glob('test/*.py'),
		ut_str        = '${PYTHON} -B -m unittest discover')

"""

import os
from waflib import Task, TaskGen, Errors, Utils, Logs
from waflib.Tools import ccroot

from waflib import Node
def _process_use_rec(self, name):
	"""
	Recursively process ``use`` for task generator with name ``name``..
	Used by pytest_process_use.
	"""
	if name in self.pytest_use_not or name in self.pytest_use_seen:
		return
	try:
		tg = self.bld.get_tgen_by_name(name)
	except Errors.WafError:
		self.pytest_use_not.add(name)
		return

	self.pytest_use_seen.append(name)
	tg.post()

	for n in self.to_list(getattr(tg, 'use', [])):
		_process_use_rec(self, n)


@TaskGen.feature('pytest')
@TaskGen.after_method('process_source', 'apply_link')
def pytest_process_use(self):
	"""
	Process the ``use`` attribute which contains a list of task generator names and store
	paths that later is used to populate the unit test runtime environment.
	"""
	self.pytest_use_not = set()
	self.pytest_use_seen = []
	self.pytest_paths = []
	self.pytest_libpaths = []
	self.pytest_dep_nodes = []

	names = self.to_list(getattr(self, 'use', []))
	for name in names:
		_process_use_rec(self, name)
	
	def extend_unique(lst, var):
		ext = []
		for x in Utils.to_list(var):
			#if isinstance(x, str):
			#	x = x.abspath()
			if x not in lst:
				ext.append(x)
		lst.extend(ext)

	# Collect type specific info needed to construct a valid runtime environment
	# for the test.
	for name in self.pytest_use_seen:
		tg = self.bld.get_tgen_by_name(name)

		extend_unique(self.pytest_paths, getattr(tg, 'pytest_path', []))
		extend_unique(self.pytest_libpaths, getattr(tg, 'pytest_libpath', []))

		if 'py' in tg.features:
			# Python dependencies are added to PYTHONPATH
			# Use only py-files in multi language sources

			if 'buildcopy' in tg.features:
				# Since buildcopy is used we assume that PYTHONPATH in build should be used,
				# not source
				pypath = getattr(tg, 'install_from', tg.path)
				extend_unique(self.pytest_paths, pypath.get_bld().abspath())

				# Add builcopy output nodes to dependencies
				extend_unique(self.pytest_dep_nodes, [o for task in getattr(tg, 'tasks', []) \
														for o in getattr(task, 'outputs', [])])
			else:
				# If buildcopy is not used, depend on sources instead
				extend_unique(self.pytest_dep_nodes, [s for s in tg.source if s.suffix() == '.py'])

				pypath = getattr(tg, 'install_from', tg.path)
				extend_unique(self.pytest_paths, pypath.abspath())

		if getattr(tg, 'link_task', None):
			# For tasks with a link_task (C, C++, D et.c.) include their LIBPATH and if it's
			# a Python extension, also add to PYTHONPATH.
			if not isinstance(tg.link_task, ccroot.stlink_task):
				extend_unique(self.pytest_dep_nodes, tg.link_task.outputs)
				extend_unique(self.pytest_libpaths, tg.link_task.env.LIBPATH)

				if 'pyext' in tg.features:
					# If the taskgen is extending Python we also want to add it o the PYTHONPATH.
					extend_unique(self.pytest_libpaths, tg.link_task.env.LIBPATH_PYEXT)


@TaskGen.feature('pytest')
@TaskGen.after_method('pytest_process_use')
def make_pytest(self):
	"""
	Creates a ``utest`` task with a populated environment for Python if not specified in ``ut_env``:

	- Paths in `pytest_paths` attribute are used to populate PYTHONPATH
	- Paths in `pytest_libpaths` attribute are used to populate the system library path (e.g. LD_LIBRARY_PATH)
	"""
	nodes = self.to_nodes(self.pytest_source)
	tsk = self.create_task('utest', nodes)
	
	tsk.dep_nodes.extend(self.pytest_dep_nodes)
	if getattr(self, 'ut_str', None):
		self.ut_run, lst = Task.compile_fun(self.ut_str, shell=getattr(self, 'ut_shell', False))
		tsk.vars = lst + tsk.vars

	if getattr(self, 'ut_cwd', None):
		if isinstance(self.ut_cwd, str):
			# we want a Node instance
			if os.path.isabs(self.ut_cwd):
				self.ut_cwd = self.bld.root.make_node(self.ut_cwd)
			else:
				self.ut_cwd = self.path.make_node(self.ut_cwd)
	else:
		if tsk.inputs:
			self.ut_cwd = tsk.inputs[0].parent
		else:
			raise Errors.WafError("no valid input files for pytest task, check pytest_source value")

	if not self.ut_cwd.exists():
		self.ut_cwd.mkdir()

	if not hasattr(self, 'ut_env'):
		self.ut_env = dict(os.environ)
		def add_paths(var, lst):
			# Add list of paths to a variable, lst can contain strings or nodes
			lst = [ str(n) for n in lst ]
			Logs.debug("ut: %s: Adding paths %s=%s", self, var, lst)
			self.ut_env[var] = os.pathsep.join(lst) + os.pathsep + self.ut_env.get(var, '')

		# Prepend dependency paths to PYTHONPATH and LD_LIBRARY_PATH
		add_paths('PYTHONPATH', self.pytest_paths)

		if Utils.is_win32:
			add_paths('PATH', self.pytest_libpaths)
		elif Utils.unversioned_sys_platform() == 'darwin':
			add_paths('DYLD_LIBRARY_PATH', self.pytest_libpaths)
			add_paths('LD_LIBRARY_PATH', self.pytest_libpaths)
		else:
			add_paths('LD_LIBRARY_PATH', self.pytest_libpaths)

