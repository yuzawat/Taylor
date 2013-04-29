from setuptools import setup, find_packages
import sys, os

version = '0.1'

setup(name='Taylor',
      version=version,
      description="swift built-in object manupulator",
      long_description="""\
""",
      classifiers=[], # Get strings from http://pypi.python.org/pypi?%3Aaction=list_classifiers
      keywords='openstack swift wsgi middleware',
      author='yuzawat',
      author_email='suzdalenator@gmail.com',
      url='',
      license='Apache License 2',
      packages=find_packages(exclude=['ez_setup', 'examples', 'tests']),
      package_data = {'': ['templates/*.tmpl', 'images/*.*', 'js/*.js', 'css/*.css']},
      include_package_data=True,
      zip_safe=False,
      install_requires=[
          'mako',
          'swift >= 1.8.0',
          'python-swiftclient',
      ],
      entry_points= {
        'paste.filter_factory': [
            'taylor=taylor.taylor:filter_factory',
            ],
        },
      )
