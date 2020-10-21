const path = require('path')
const webpack = require('webpack')
const HtmlWebpackPlugin = require('html-webpack-plugin');
const CopyPlugin = require('copy-webpack-plugin');

process.traceDeprecation = true;

module.exports = {
	watch: true,
	entry: {
		app: './src/js/main.js'
	},
	performance: {
		hints: false
	},
	output: {
		path: path.resolve(__dirname, './dist'),
		publicPath: '/',
		filename: '[name].js',
		chunkFilename: '[name].bundle.js'
	},
	optimization: {
		splitChunks: {
			chunks: 'all'
		}
	},
	module: {
		rules: [
			{
				test: /\.scss$/,
				use: [
				  {
				      loader: "style-loader" // creates style nodes from JS strings
				  },
				  {
				      loader: "css-loader" // translates CSS into CommonJS
				  },
				  {
				      loader: "sass-loader" // compiles Sass to CSS
				  }
				]
			},
			{
				test: /\.css$/,
				use: [ 'style-loader', 'css-loader' ]
			},
			{
				test: /\.woff(2)?(\?v=[0-9]\.[0-9]\.[0-9])?$/,
				loader: "url-loader?limit=10000&mimetype=application/font-woff",
				options: {
					name: 'assets/[name].[ext]?[hash]',
					outputPath: path.resolve(__dirname, './dist/assets'),
					publicPath: '/'
				}
			},
			{
				test: /\.(ttf|eot)(\?v=[0-9]\.[0-9]\.[0-9])?$/, //test selector originally also included svg-files
				loader: "file-loader",
				options: {
					name: '[name].[ext]?[hash]',
					outputPath: 'assets/',
					publicPath: '/'
				}
			},
			{
				test: /\.js$/,
				exclude: [
					/(node_modules|bower_components)/,
					path.resolve(__dirname, './src/config/config.js')
				],
				use: {
					loader: 'babel-loader'
				}
			},
		    {
		        test: /\.(html)$/i,
		        loader: 'html-loader'
		    },
		    {
				test: /\.(png|jpe?g|gif|svg)$/i,
				loader: 'file-loader',
				options: {
					name: '[name].[ext]?[hash]',
					outputPath: 'assets/',
					publicPath: '/assets/'
				}
		    },
			{
				test: /\.(ico)$/i,
				loader: 'file-loader',
				options: {
					name: '[name].[ext]?[hash]',
					outputPath: 'assets/icons/',
					publicPath: '/assets/icons/'
				}
			},
			{
				test: /\.webmanifest$/,
				use: [
					{
						loader: "file-loader",
						options: {
							name: '[name].[ext]?[hash]',
							outputPath: '',
							publicPath: '/'
						}
					}
				]
			}
		]
	},
	resolve: {
	},
	devServer: {
		historyApiFallback: true,
		noInfo: true
	},
	performance: {
		hints: "warning"
	},
	devtool: '#eval-source-map',
	plugins: [
		new webpack.ProvidePlugin({
		  $: "jquery",
		  jQuery: "jquery"
		}),
		new HtmlWebpackPlugin({
			template: 'src/index.html',
			hash: true
		}),
		new CopyPlugin({
			patterns: [
			  { from: 'src/config/config.json', to: 'config.json' },
			  { from : 'src/php', to: 'php' },
			  { from: 'src/index.php', to: 'index.php' }
			],
		})
	]
}

//For local development
if (process.env.NODE_ENV === 'development') {
	module.exports.devtool = '#source-map'
	module.exports.plugins = (module.exports.plugins || []).concat([
		new webpack.DefinePlugin({
			'process.env': {
				NODE_ENV: '"development"'
			}
		}),
		new webpack.LoaderOptionsPlugin({
			minimize: false
		})
	])
}

//For development releases
if (process.env.NODE_ENV === 'development-release') {
	module.exports.devtool = '#source-map'
	module.exports.plugins = (module.exports.plugins || []).concat([
		new webpack.DefinePlugin({
			'process.env': {
				NODE_ENV: '"development-release"'
			}
		}),
	])
}


//For production releases
if (process.env.NODE_ENV === 'production') {
  //module.exports.devtool = '#source-map'
  module.exports.plugins = (module.exports.plugins || []).concat([
    new webpack.DefinePlugin({
      'process.env': {
        NODE_ENV: '"production"'
      }
    })
  ])
}
