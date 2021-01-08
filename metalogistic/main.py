import numpy as np
from scipy import optimize
from scipy import stats
import matplotlib.pyplot as plt
import warnings

class MetaLogistic(stats.rv_continuous):
	'''
	We subclass scipy.stats.rv_continuous so we can make use of all the nice SciPy methods. We redefine the private methods
	_cdf, _pdf, and _ppf, and SciPy will make calls to these whenever needed.
	'''
	def __init__(self, cdf_ps=None, cdf_xs=None, term=None, fit_method=None, lbound=None, ubound=None, a_vector=None, feasibility_method='SmallMReciprocal'):
		'''
		:param cdf_ps: Probabilities of the CDF input data.
		:param cdf_xs: X-values of the CDF input data (the pre-images of the probabilities).
		:param term: Produces a `term`-term metalog. Cannot be greater than the number of CDF points provided. By default, it is
		equal to the number of CDF points.
		:param fit_method: Set to 'LLS' to allow linear least squares only. By default, numerical methods are tried if LLS fails.
		:param lbound: Lower bound
		:param ubound: Upper bound
		:param a_vector: You may supply the a-vector directly, in which case the input data `cdf_ps` and `cdf_xs` are not used for fitting.
		'''
		warnings.filterwarnings("ignore", category=UserWarning, module='scipy.optimize')

		super(MetaLogistic, self).__init__()

		if lbound is None and ubound is None:
			self.boundedness = False
		if lbound is None and ubound is not None:
			self.boundedness = 'upper'
		if lbound is not None and ubound is None:
			self.boundedness = 'lower'
		if lbound is not None and ubound is not None:
			self.boundedness = 'bounded'
		self.lbound = lbound
		self.ubound = ubound

		self.fit_method_requested = fit_method
		self.numeric_ls_solver_used = None
		self.feasibility_method = feasibility_method

		self.cdf_ps = cdf_ps
		self.cdf_xs = cdf_xs
		if cdf_xs is not None and cdf_ps is not None:
			self.cdf_p_x_mapping = {cdf_ps[i]: cdf_xs[i] for i in range(len(cdf_ps))}
			self.cdf_len = len(cdf_ps)
			self.cdf_ps = np.asarray(self.cdf_ps)
			self.cdf_xs = np.asarray(self.cdf_xs)

		# Special case where a MetaLogistic object is created by supplying the a-vector directly.
		if a_vector is not None:
			self.a_vector = a_vector
			if term is None:
				self.term = len(a_vector)
			else:
				self.term = term
			return

		if len(cdf_ps) != len(cdf_ps):
			raise ValueError("cdf_ps and cdf_xs must have the same length")

		if term is None:
			self.term = self.cdf_len
		else:
			self.term = term


		self.constructZVec()
		self.constructYMatrix()

		#  Try linear least squares
		self.fitLinearLeastSquares()

		# If linear least squares result is feasible
		if self.isFeasible():
			self.fit_method_used = 'LLS'
			self.valid_distribution = True

		#  If linear least squares result is not feasible
		else:
			# if the user allows it, use numerical least squares
			if not fit_method == 'LLS':
				self.fit_method_used = 'numeric'
				# TODO: set a timeout (e.g. 1 second) for the call to fitNumericLeastSquares(). If the call
				# times out with the default feasibility method, iterate through all possible methods,
				# keeping the best result. This is because, in my experience, if a method will succeed, it succeeds
				# within hundreds of milliseconds; if it's been going on for more than a second, another method will likely give
				# good results faster.
				self.fitNumericLeastSquares(feasibility_method=self.feasibility_method)

			# If only LLS is allowed, we cannot find a valid metalog
			else:
				self.valid_distribution = False

		if not self.isFeasible():
			print("Warning: the program was not able to fit a valid metalog distribution for your data.")


	def isFeasible(self):
		if self.feasibility_method == 'QuantileMinimumIncrement':
			s = self.QuantileMinimumIncrement()
			if s<0:
				self.valid_distribution_violation = s
				self.valid_distribution = False
			else:
				self.valid_distribution_violation = 0
				self.valid_distribution = True

		if self.feasibility_method == 'QuantileSumNegativeIncrements':
			s = self.infeasibilityScoreQuantileSumNegativeIncrements()
			self.valid_distribution_violation = s
			self.valid_distribution = s==0

		if self.feasibility_method == 'SmallMReciprocal':
			s = self.infeasibilityScoreSmallMReciprocal()
			self.valid_distribution_violation = s
			self.valid_distribution = s==0

		return self.valid_distribution


	def fitLinearLeastSquares(self):
		'''
		Constructs the a-vector by linear least squares, as defined in Keelin 2016, Equation 7 (unbounded case), Equation 12 (semi-bounded case).

		'''
		left = np.linalg.inv(np.dot(self.YMatrix.T, self.YMatrix))
		right = np.dot(self.YMatrix.T, self.z_vec)

		self.a_vector = np.dot(left, right)

	def fitNumericLeastSquares(self, feasibility_method):
		bounds_kwargs = {}
		if self.lbound is not None:
			bounds_kwargs['lbound'] = self.lbound
		if self.ubound is not None:
			bounds_kwargs['ubound'] = self.ubound

		def loss_function(a_candidate):
			# Setting a_vector in this MetaLogistic call overrides the cdf_ps and cdf_xs arguments, which are only used
			# for meanSquareError().
			return MetaLogistic(self.cdf_ps, self.cdf_xs, **bounds_kwargs, a_vector=a_candidate).meanSquareError()

		# Choose the method of determining feasibility.
		def feasibilityViaCDFSumNegative(a_candidate):
			return MetaLogistic(a_vector=a_candidate, **bounds_kwargs).infeasibilityScoreQuantileSumNegativeIncrements()

		def feasibilityViaQuantileMinimumIncrement(a_candidate):
			return MetaLogistic(a_vector=a_candidate, **bounds_kwargs).QuantileMinimumIncrement()

		def feasibilityViaSmallMReciprocal(a_candidate):
			return MetaLogistic(a_vector=a_candidate, **bounds_kwargs).infeasibilityScoreSmallMReciprocal()

		if feasibility_method == 'SmallMReciprocal':
			def feasibilityBool(a_candidate):
				return feasibilityViaSmallMReciprocal(a_candidate) == 0
			feasibility_constraint = optimize.NonlinearConstraint(feasibilityViaSmallMReciprocal, 0, 0)

		if feasibility_method == 'QuantileSumNegativeIncrements':
			def feasibilityBool(a_candidate):
				return feasibilityViaCDFSumNegative(a_candidate) == 0
			feasibility_constraint = optimize.NonlinearConstraint(feasibilityViaCDFSumNegative, 0, 0)

		if feasibility_method == 'QuantileMinimumIncrement':
			def feasibilityBool(a_candidate):
				return feasibilityViaQuantileMinimumIncrement(a_candidate) >= 0
			feasibility_constraint = optimize.NonlinearConstraint(feasibilityViaQuantileMinimumIncrement, 0, np.inf)

		a0 = self.a_vector

		# First, try the default solver, which is often fast and accurate
		options = {}
		optimize_results = optimize.minimize(loss_function,
											 a0,
											 constraints=feasibility_constraint,
											 options=options)
		self.numeric_ls_solver_used = 'Default'

		# If the mean square error is too large or distribution invalid, try the trust-constr solver
		if optimize_results.fun > 0.01 or not feasibilityBool(optimize_results.x):
			options = {'xtol':1e-6}
			a0 = optimize_results.x
			optimize_results_alternate = optimize.minimize(loss_function,
												 a0,
												 constraints=feasibility_constraint,
												 method='trust-constr',
												 options=options)
			self.numeric_ls_solver_used = 'trust-constr'

			if optimize_results_alternate.constr_violation == 0:
				if optimize_results_alternate.fun < optimize_results.fun:
					optimize_results = optimize_results_alternate
				else:
					optimize_results = optimize_results


		self.a_vector = optimize_results.x
		self.numeric_leastSQ_OptimizeResult = optimize_results

	def meanSquareError(self):
		ps_on_fitted_cdf = self.cdf(self.cdf_xs)
		sum_sq_error = np.sum((self.cdf_ps - ps_on_fitted_cdf) ** 2)
		return sum_sq_error/self.cdf_len

	def infeasibilityScoreQuantileSumNegativeIncrements(self):
		check_ys_from = 0.001
		number_to_check = 200  # This parameter is very important to both performance and correctness.
		ps_to_check = np.linspace(check_ys_from, 1 - check_ys_from, number_to_check)
		xs_to_check = self.quantile(ps_to_check)
		prev = -np.inf
		infeasibility_score = 0
		for item in xs_to_check:
			diff = item - prev
			if diff < 0:
				# Logarithm of the difference, to keep this scale-free
				infeasibility_score += np.log(1-diff)
			prev = item
		return infeasibility_score

	def infeasibilityScoreSmallMReciprocal(self):
		check_ys_from = 0.001
		number_to_check = 100
		ps_to_check = np.linspace(check_ys_from, 1 - check_ys_from, number_to_check)

		densities_to_check = self.densitySmallM(ps_to_check)
		densities_reciprocal = 1/densities_to_check
		infeasibility_score = np.abs(np.sum(densities_reciprocal[densities_reciprocal<0]))

		return infeasibility_score

	def CDFSlopeNumeric(self, p):
		epsilon = 1e-5
		if not np.isfinite(self.quantile(p+epsilon)):
			epsilon = -epsilon

		cdfSlope = optimize.approx_fprime(p,self.quantile,epsilon)
		return cdfSlope

	def QuantileMinimumIncrement(self):
		# Get a good initial guess
		check_ys_from = 0.001
		number_to_check = 100
		ps_to_check = np.linspace(check_ys_from, 1 - check_ys_from, number_to_check)
		xs = self.quantile(ps_to_check)
		xs_diff = np.diff(xs)
		i = np.argmin(xs_diff)
		p0 = ps_to_check[i]

		# Do the minimization
		r = optimize.minimize(self.CDFSlopeNumeric, x0=p0, bounds=[(0, 1)])
		return r.fun



	def constructZVec(self):
		'''
		Constructs the z-vector, as defined in Keelin 2016, Section 3.3. (unbounded case, where it is called the `x`-vector),
		Section 4.1 (semi-bounded case), and Section 4.3 (bounded case).

		This vector is a transformation of cdf_xs to account for bounded or semi-bounded distributions.
		When the distribution is unbounded, the z-vector is simply equal to cdf_xs.
		'''
		if not self.boundedness:
			self.z_vec = self.cdf_xs
		if self.boundedness == 'lower':
			self.z_vec = np.log(self.cdf_xs-self.lbound)
		if self.boundedness == 'upper':
			self.z_vec = -np.log(self.ubound - self.cdf_xs)
		if self.boundedness == 'bounded':
			self.z_vec = np.log((self.cdf_xs-self.lbound)/(self.ubound-self.cdf_xs))

	def constructYMatrix(self):
		'''
		Constructs the Y-matrix, as defined in Keelin 2016, Equation 8.
		'''

		# The series of Y_n matrices. Although we only return the last matrix in the series, the entire series is necessary to construct it
		Y_ns = {}
		ones = np.ones(self.cdf_len).reshape(self.cdf_len, 1)
		column_2 = np.log(self.cdf_ps / (1 - self.cdf_ps)).reshape(self.cdf_len, 1)
		column_4 = (self.cdf_ps - 0.5).reshape(self.cdf_len, 1)
		Y_ns[2] = np.hstack([ones, column_2])
		Y_ns[3] = np.hstack([Y_ns[2], column_4 * column_2])
		Y_ns[4] = np.hstack([Y_ns[3], column_4])

		if (self.term > 4):
			for n in range(5, self.term + 1):
				if n % 2 != 0:
					new_column = column_4 ** ((n - 1) / 2)
					Y_ns[n] = np.hstack([Y_ns[n - 1], new_column])

				if n % 2 == 0:
					new_column = (column_4 ** (n / 2 - 1)) * column_2
					Y_ns[n] = np.hstack([Y_ns[n - 1], new_column])

		self.YMatrix = Y_ns[self.term]

	def _quantile(self, probability, force_unbounded=False):
		'''
		The metalog inverse CDF, or quantile function, as defined in Keelin 2016, Equation 6 (unbounded case), Equation 11 (semi-bounded case),
		and Equation 14 (bounded case).

		`probability` must be a scalar.
		'''

		# if not 0 <= probability <= 1:
		# 	raise ValueError("Probability in call to quantile() must be between 0 and 1")

		if probability <= 0:
			if (self.boundedness == 'lower' or self.boundedness == 'bounded') and not force_unbounded:
				return self.lbound
			else:
				return -np.inf

		if probability >= 1:
			if (self.boundedness == 'upper' or self.boundedness == 'bounded') and not force_unbounded:
				return self.ubound
			else:
				return np.inf

		# `self.a_vector` is 0-indexed, while in Keelin 2016 the a-vector is 1-indexed.
		# To make this method as easy as possible to read if following along with the paper, I create a dictionary `a`
		# that mimics a 1-indexed vector.
		a = {i + 1: element for i, element in enumerate(self.a_vector)}

		# The series of quantile functions. Although we only return the last result in the series, the entire series is necessary to construct it
		ln_p_term = np.log(probability / (1 - probability))
		p05_term = probability - 0.5
		quantile_functions = {}

		quantile_functions[2] = a[1] + a[2] * ln_p_term
		quantile_functions[3] = quantile_functions[2] + a[3] * p05_term * ln_p_term
		if self.term>3:
			quantile_functions[4] = quantile_functions[3] + a[4] * p05_term

		if (self.term > 4):
			for n in range(5, self.term + 1):
				if n % 2 != 0:
					quantile_functions[n] = quantile_functions[n - 1] + a[n] * p05_term ** ((n - 1) / 2)

				if n % 2 == 0:
					quantile_functions[n] = quantile_functions[n - 1] + a[n] * p05_term ** (n / 2 - 1) * ln_p_term

		quantile_function = quantile_functions[self.term]

		if not force_unbounded:
			if self.boundedness == 'lower':
				quantile_function = self.lbound + np.exp(quantile_function)  # Equation 11
			if self.boundedness == 'upper':
				quantile_function = self.ubound - np.exp(-quantile_function)
			if self.boundedness == 'bounded':
				quantile_function = (self.lbound+self.ubound*np.exp(quantile_function))/(1+np.exp(quantile_function))  # Equation 14

		return quantile_function

	def densitySmallM(self,cumulative_prob,force_unbounded=False):
		'''
		This is the metalog PDF as a function of cumulative probability, as defined in Keelin 2016, Equation 9 (unbounded case),
		Equation 13 (semi-bounded case).

		Notice the unusual definition of the PDF, which is why I call this function densitySmallM in reference to the notation in
		Keelin 2016.
		'''

		if self.isListLike(cumulative_prob):
			return np.asarray([self.densitySmallM(i) for i in cumulative_prob])

		if not 0 <= cumulative_prob <= 1:
			raise ValueError("Probability in call to densitySmallM() must be between 0 and 1")
		if not self.boundedness and (cumulative_prob==0 or cumulative_prob==1):
			raise ValueError("Probability in call to densitySmallM() cannot be equal to 0 and 1 for an unbounded distribution")


		# The series of density functions. Although we only return the last result in the series, the entire series is necessary to construct it
		density_functions = {}

		# `self.a_vector` is 0-indexed, while in Keelin 2016 the a-vector is 1-indexed.
		# To make this method as easy as possible to read if following along with the paper, I create a dictionary `a`
		# that mimics a 1-indexed vector.
		a = {i + 1: element for i, element in enumerate(self.a_vector)}

		ln_p_term = np.log(cumulative_prob / (1 - cumulative_prob))
		p05_term = cumulative_prob - 0.5
		p1p_term = cumulative_prob*(1-cumulative_prob)

		density_functions[2] = p1p_term/a[2]
		density_functions[3] = 1/(1/density_functions[2] + a[3]*(p05_term/p1p_term+ln_p_term))
		if self.term>3:
			density_functions[4] = 1/(1/density_functions[3] + a[4])

		if (self.term > 4):
			for n in range(5, self.term + 1):
				if n % 2 != 0:
					density_functions[n] = 1/(1/density_functions[n-1]+ a[n]*((n-1)/2)*p05_term**((n-3)/2))

				if n % 2 == 0:
					density_functions[n] = 1/(1/density_functions[n-1] + a[n](p05_term**(n/2-1)/p1p_term +
																			  (n/2-1)*p05_term**(n/2-2)*ln_p_term)
											  )

		density_function = density_functions[self.term]
		if not force_unbounded:
			if self.boundedness == 'lower':   # Equation 13
				if 0<cumulative_prob<1:
					density_function = density_function * np.exp(-self._quantile(cumulative_prob, force_unbounded=True))
				elif cumulative_prob == 0:
					density_function = 0
				else:
					raise ValueError("Probability in call to densitySmallM() cannot be equal to 1 with a lower-bounded distribution.")

			if self.boundedness == 'upper':
				if 0 < cumulative_prob < 1:
					density_function = density_function * np.exp(self._quantile(cumulative_prob, force_unbounded=True))
				elif cumulative_prob == 1:
					density_function = 0
				else:
					raise ValueError("Probability in call to densitySmallM() cannot be equal to 0 with a upper-bounded distribution.")

			if self.boundedness == 'bounded':  # Equation 15
				if 0 < cumulative_prob < 1:
					x_unbounded = np.exp(self._quantile(cumulative_prob, force_unbounded=True))
					density_function = density_function * (1 + x_unbounded)**2 / ((self.ubound - self.lbound) * x_unbounded)
				if cumulative_prob==0 or cumulative_prob==1:
					density_function = 0

		return density_function

	def getCumulativeProb(self, x):
		'''
		The metalog is defined in terms of its inverse CDF or quantile function. In order to get probabilities for a given x-value,
		like in a traditional CDF, we invert this quantile function using a numerical equation solver.

		`x` must be a scalar
		'''
		f_to_zero = lambda probability: self._quantile(probability) - x
		return optimize.brentq(f_to_zero, 0, 1, disp=True)

	def _cdf(self, x):
		'''
		This is where we override the SciPy method for the CDF.

		`x` may be a scalar or list-like.
		'''
		if self.isListLike(x):
			return [self._cdf(i) for i in x]
		if self.isNumeric(x):
			return self.getCumulativeProb(x)

	def _ppf(self, probability):
		'''
		This is where we override the SciPy method for the inverse CDF or quantile function (ppf stands for percent point function)

		`probability` may be a scalar or list-like.
		'''
		if self.isListLike(probability):
			return np.asarray([self._ppf(i) for i in probability])

		if self.isNumeric(probability):
			return self._quantile(probability)

	def quantile(self, probability):
		'''
		An alias for ppf, because 'percent point function' is somewhat non-standard terminology
		'''
		return self._ppf(probability)

	def _pdf(self, x):
		'''
		This is where we override the SciPy method for the PDF.

		`x` may be a scalar or list-like.
		'''
		if self.isListLike(x):
			return [self._pdf(i) for i in x]

		if self.isNumeric(x):
			cumulative_prob = self.getCumulativeProb(x)
			return self.densitySmallM(cumulative_prob)

	@staticmethod
	def isNumeric(object):
		return isinstance(object, (float, int)) or (isinstance(object,np.ndarray) and object.ndim==0)

	@staticmethod
	def isListLike(object):
		return isinstance(object, list) or (isinstance(object,np.ndarray) and object.ndim==1)

	def printSummary(self):
		# print("Fit method requested:", self.fit_method_requested)
		print("Fit method used:", self.fit_method_used)
		print("Distribution is valid:", self.valid_distribution)
		print("Method for determining distribution validity:", self.feasibility_method)
		if not self.valid_distribution:
			print("Distribution validity constraint violation:", self.valid_distribution_violation)
		if not self.fit_method_used == 'LLS':
			print("Solver for numeric fit:", self.numeric_ls_solver_used)
			print("Solver convergence:", self.numeric_leastSQ_OptimizeResult.success)
			# print("Solver convergence message:", self.numeric_leastSQ_OptimizeResult.message)
		print("Mean square error:", self.meanSquareError())
		print('a vector:', self.a_vector)

	def createCDFPlotData(self,p_from=0.001,p_to=0.999,n=100):
		cdf_ps = np.linspace(p_from,p_to,n)
		cdf_xs = self.quantile(cdf_ps)

		return {'X-values':cdf_xs,'Probabilities':cdf_ps}

	def createPDFPlotData(self, p_from=0.001, p_to=0.999, n=100):
		pdf_ps = np.linspace(p_from, p_to, n)
		pdf_xs = self.quantile(pdf_ps)
		pdf_densities = self.densitySmallM(pdf_ps)

		return {'X-values': pdf_xs, 'Densities': pdf_densities}

	def displayPlot(self, p_from_to=0.001, x_from_to=(None,None), n=100, hide_extreme_densities=50):
		if isinstance(p_from_to, (float,int)):
			p_from = p_from_to
			p_to = 1 - p_from_to
		if isinstance(p_from_to, tuple):
			p_from,p_to = p_from_to

		x_from, x_to = x_from_to
		if x_from is not None and x_to is not None:
			p_from = self.getCumulativeProb(x_from)
			p_to = self.getCumulativeProb(x_to)

		fig, (cdf_axis, pdf_axis) = plt.subplots(2)
		fig.set_size_inches(10, 10)

		cdf_data = self.createCDFPlotData(p_from,p_to,n)
		cdf_axis.plot(cdf_data['X-values'],cdf_data['Probabilities'])
		if self.cdf_xs is not None and self.cdf_ps is not None:
			cdf_axis.scatter(self.cdf_xs, self.cdf_ps, marker='+', color='red')
		cdf_axis.set_title('CDF')

		pdf_data = self.createPDFPlotData(p_from,p_to,n)
		pdf_axis.set_title('PDF')

		if hide_extreme_densities:
			density50 = np.percentile(pdf_data['Densities'],50)
			pdf_max_display = min(density50*hide_extreme_densities,1.05*max(pdf_data['Densities']))
			pdf_axis.set_ylim(top=pdf_max_display)

		pdf_axis.plot(pdf_data['X-values'], pdf_data['Densities'])


		fig.show()
		return fig
